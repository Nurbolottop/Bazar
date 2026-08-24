"""Уведомления: шаблоны, очередь, напоминания, отправка push (ТЗ-02 п. 9.2, ТЗ-00 п. 5.6)."""
import datetime
import logging

from django.conf import settings as django_settings
from django.db.models import F
from django.utils import timezone

from apps.billing.models import Charge
from apps.core.models import SystemSettings
from apps.core.money import ZERO
from apps.tenants.models import Tenant

from .models import Announcement, Notification, NotificationCode, NotificationTemplate

logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 3

# Встроенные шаблоны — используются, пока администрация не отредактировала свои (FR-NT-07)
DEFAULT_TEMPLATES = {
    NotificationCode.CHARGE_CREATED: {
        'ru': ('Новое начисление',
               '{name}, вам начислена аренда {amount} сом. Оплатите до {date}.'),
        'ky': ('Жаңы эсептөө',
               '{name}, сизге {amount} сом ижана эсептелди. {date} чейин төлөңүз.'),
    },
    NotificationCode.REMINDER_10: {
        'ru': ('Срок оплаты приближается',
               '{name}, до срока оплаты аренды осталось 10 дней. К оплате: {amount} сом до {date}.'),
        'ky': ('Төлөө мөөнөтү жакындап калды',
               '{name}, ижана төлөөгө 10 күн калды. Төлөм: {amount} сом, {date} чейин.'),
    },
    NotificationCode.REMINDER_5: {
        'ru': ('Срок оплаты приближается',
               '{name}, до срока оплаты аренды осталось 5 дней. К оплате: {amount} сом до {date}.'),
        'ky': ('Төлөө мөөнөтү жакындап калды',
               '{name}, ижана төлөөгө 5 күн калды. Төлөм: {amount} сом, {date} чейин.'),
    },
    NotificationCode.REMINDER_3: {
        'ru': ('Срок оплаты приближается',
               '{name}, до срока оплаты аренды осталось 3 дня. К оплате: {amount} сом до {date}.'),
        'ky': ('Төлөө мөөнөтү жакындап калды',
               '{name}, ижана төлөөгө 3 күн калды. Төлөм: {amount} сом, {date} чейин.'),
    },
    NotificationCode.REMINDER_0: {
        'ru': ('Сегодня срок оплаты',
               '{name}, сегодня последний день оплаты аренды. К оплате: {amount} сом.'),
        'ky': ('Бүгүн төлөө мөөнөтү',
               '{name}, бүгүн ижана төлөөнүн акыркы күнү. Төлөм: {amount} сом.'),
    },
    NotificationCode.CLAIM_CONFIRMED: {
        'ru': ('Оплата подтверждена',
               '{name}, ваша оплата {amount} сом подтверждена. Задолженность погашена.'),
        'ky': ('Төлөм ырасталды',
               '{name}, сиздин {amount} сом төлөмүңүз ырасталды. Карыз жабылды.'),
    },
    NotificationCode.CLAIM_CONFIRMED_PARTIAL: {
        'ru': ('Оплата подтверждена',
               '{name}, ваша оплата {amount} сом подтверждена. Остаток к доплате: {rest} сом.'),
        'ky': ('Төлөм ырасталды',
               '{name}, сиздин {amount} сом төлөмүңүз ырасталды. Калган карыз: {rest} сом.'),
    },
    NotificationCode.CLAIM_REJECTED: {
        'ru': ('Заявка отклонена',
               '{name}, ваша заявка об оплате отклонена. Причина: {reason}. '
               'Вы можете подать заявку заново.'),
        'ky': ('Табыштама четке кагылды',
               '{name}, төлөм тууралуу табыштамаңыз четке кагылды. Себеби: {reason}. '
               'Кайра табыштама бере аласыз.'),
    },
    NotificationCode.OVERDUE: {
        'ru': ('Оплата просрочена',
               '{name}, срок оплаты аренды истёк. Задолженность: {amount} сом.'),
        'ky': ('Төлөм мөөнөтү өттү',
               '{name}, ижана төлөө мөөнөтү өттү. Карыз: {amount} сом.'),
    },
}


def format_amount(value) -> str:
    """Формат суммы «12 500» (ТЗ-00 п. 7.4)."""
    return f'{value:,.2f}'.replace(',', ' ').removesuffix('.00')


def format_date(value: datetime.date) -> str:
    return value.strftime('%d.%m.%Y')


def render(code: str, tenant: Tenant, context: dict) -> tuple[str, str]:
    """Заголовок и текст на языке арендатора; при отсутствии перевода — русский (ТЗ-02 п. 9.2)."""
    lang = tenant.language or 'ru'
    template = NotificationTemplate.objects.filter(code=code, lang=lang).first()
    if template is None and lang != 'ru':
        template = NotificationTemplate.objects.filter(code=code, lang='ru').first()
    if template is not None:
        title_tpl, body_tpl = template.title_template, template.body_template
    else:
        defaults = DEFAULT_TEMPLATES.get(code, {})
        title_tpl, body_tpl = defaults.get(lang) or defaults.get('ru') or (code, code)

    safe = _SafeDict(name=tenant.full_name, **context)
    return title_tpl.format_map(safe), body_tpl.format_map(safe)


class _SafeDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def queue(tenant: Tenant, code: str, context: dict | None = None,
          payload: dict | None = None, once_per_day: bool = False) -> Notification | None:
    """Поставить уведомление в очередь и сохранить в ленте (FR-NT-08).

    Архивные арендаторы уведомления не получают; приостановленные — только
    объявления (ТЗ-00 п. 5.2.1). once_per_day — не более одного уведомления
    данного типа в сутки (FR-NT-10).
    """
    if tenant.status == Tenant.Status.ARCHIVED:
        return None
    if tenant.status == Tenant.Status.SUSPENDED and code != NotificationCode.ANNOUNCEMENT:
        return None

    if once_per_day:
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if Notification.objects.filter(
                tenant=tenant, code=code, created_at__gte=today_start).exists():
            return None

    title, body = render(code, tenant, context or {})
    return Notification.objects.create(
        tenant=tenant, code=code, title=title, body=body,
        payload=payload or {}, status=Notification.Status.QUEUED)


# ---------------------------------------------------------------------------
# События (вызываются из сервисного слоя billing/payments)
# ---------------------------------------------------------------------------

def notify_charges_created(tenant: Tenant, due_date: datetime.date):
    """FR-NT-02: уведомление о создании начисления с суммой и сроком."""
    debt = _tenant_debt(tenant)
    queue(tenant, NotificationCode.CHARGE_CREATED, {
        'amount': format_amount(debt), 'date': format_date(due_date),
    }, payload={'event': 'charge_created'})


def notify_claim_confirmed(tenant: Tenant, amount, rest_debt):
    """FR-NT-03: текст различается — долг погашен полностью или остался остаток."""
    if rest_debt and rest_debt > ZERO:
        queue(tenant, NotificationCode.CLAIM_CONFIRMED_PARTIAL, {
            'amount': format_amount(amount), 'rest': format_amount(rest_debt),
        }, payload={'event': 'claim_confirmed'})
    else:
        queue(tenant, NotificationCode.CLAIM_CONFIRMED, {
            'amount': format_amount(amount),
        }, payload={'event': 'claim_confirmed'})


def notify_claim_rejected(tenant: Tenant, reason: str):
    """FR-NT-04: уведомление об отклонении с причиной."""
    queue(tenant, NotificationCode.CLAIM_REJECTED, {'reason': reason},
          payload={'event': 'claim_rejected'})


def _tenant_debt(tenant: Tenant):
    balance = getattr(tenant, 'balance', None)
    return balance.debt_amount if balance else ZERO


# ---------------------------------------------------------------------------
# Напоминания (команда send_reminders)
# ---------------------------------------------------------------------------

REMINDER_CODE_BY_DAYS = {
    10: NotificationCode.REMINDER_10,
    5: NotificationCode.REMINDER_5,
    3: NotificationCode.REMINDER_3,
    0: NotificationCode.REMINDER_0,
}


def send_reminders(today: datetime.date | None = None) -> int:
    """Напоминания за N дней до срока и уведомления о просрочке (FR-NT-01, FR-NT-05).

    Напоминание отправляется только при существующем неоплаченном начислении.
    Не более одного напоминания одного типа в сутки на арендатора (FR-NT-10).
    """
    today = today or timezone.localdate()
    s = SystemSettings.load()
    queued = 0

    open_charges = Charge.objects.filter(
        status__in=[Charge.Status.UNPAID, Charge.Status.PARTIAL, Charge.Status.OVERDUE],
        paid_amount__lt=F('amount'),
        tenant__status=Tenant.Status.ACTIVE,
    ).select_related('tenant')

    for days in s.reminder_days:
        code = REMINDER_CODE_BY_DAYS.get(days)
        if code is None:
            continue
        target_date = today + datetime.timedelta(days=days)
        by_tenant: dict[int, dict] = {}
        for charge in open_charges.filter(due_date=target_date):
            info = by_tenant.setdefault(
                charge.tenant_id, {'tenant': charge.tenant, 'amount': ZERO})
            info['amount'] += charge.remaining
        for info in by_tenant.values():
            notification = queue(info['tenant'], code, {
                'amount': format_amount(info['amount']),
                'date': format_date(target_date),
            }, payload={'event': 'reminder', 'days': days}, once_per_day=True)
            if notification:
                queued += 1

    for days in s.overdue_notice_days:
        target_date = today - datetime.timedelta(days=days)
        by_tenant = {}
        for charge in open_charges.filter(due_date=target_date):
            info = by_tenant.setdefault(
                charge.tenant_id, {'tenant': charge.tenant, 'amount': ZERO})
            info['amount'] += charge.remaining
        for info in by_tenant.values():
            notification = queue(info['tenant'], NotificationCode.OVERDUE, {
                'amount': format_amount(info['amount']),
            }, payload={'event': 'overdue'}, once_per_day=True)
            if notification:
                queued += 1

    return queued


# ---------------------------------------------------------------------------
# Объявления (FR-NT-06)
# ---------------------------------------------------------------------------

def publish_announcement(announcement: Announcement) -> int:
    """Доставка объявления адресатам записями Notification (ТЗ-02 п. 3.4)."""
    from apps.payments.models import TenantBalance

    tenants = Tenant.objects.exclude(status=Tenant.Status.ARCHIVED).filter(
        announcements_enabled=True)
    if announcement.audience == Announcement.Audience.BUILDING and announcement.building_id:
        tenants = tenants.filter(
            tenant_spots__is_active=True,
            tenant_spots__spot__building_id=announcement.building_id).distinct()
    elif announcement.audience == Announcement.Audience.DEBTORS:
        debtor_ids = TenantBalance.objects.filter(
            debt_amount__gt=ZERO).values_list('tenant_id', flat=True)
        tenants = tenants.filter(pk__in=list(debtor_ids))

    count = 0
    for tenant in tenants:
        title = announcement.title_ky if tenant.language == 'ky' and announcement.title_ky \
            else announcement.title_ru
        body = announcement.body_ky if tenant.language == 'ky' and announcement.body_ky \
            else announcement.body_ru
        Notification.objects.create(
            tenant=tenant, code=NotificationCode.ANNOUNCEMENT,
            title=title, body=body,
            payload={'event': 'announcement', 'announcement_id': announcement.pk})
        count += 1

    announcement.sent_at = timezone.now()
    announcement.save(update_fields=['sent_at', 'updated_at'])
    return count


# ---------------------------------------------------------------------------
# Отправка push (команда send_pending_notifications)
# ---------------------------------------------------------------------------

def send_pending() -> tuple[int, int]:
    """Отправка уведомлений из очереди; повтор неудачных не более 3 раз (ТЗ-02 раздел 8)."""
    sent = failed = 0
    pending = Notification.objects.filter(
        status__in=[Notification.Status.QUEUED, Notification.Status.FAILED],
        attempts__lt=MAX_SEND_ATTEMPTS,
    ).select_related('tenant')

    for notification in pending:
        ok, error = _send_push(notification)
        notification.attempts += 1
        if ok:
            notification.status = Notification.Status.SENT
            notification.sent_at = timezone.now()
            notification.error = ''
            sent += 1
        else:
            notification.status = Notification.Status.FAILED
            notification.error = error[:1000]
            failed += 1
        notification.save(update_fields=['status', 'sent_at', 'error', 'attempts', 'updated_at'])
    return sent, failed


def _send_push(notification: Notification) -> tuple[bool, str]:
    """Отправка через Firebase Admin SDK. В dev-контуре push не отправляются."""
    if not django_settings.PUSH_ENABLED:
        return True, ''  # контур без push: уведомление остаётся в ленте приложения

    from apps.accounts.models import Device
    devices = Device.objects.filter(tenant=notification.tenant, is_active=True) \
                            .exclude(push_token='')
    if not devices.exists():
        return True, ''  # нет устройств — не ошибка, лента приложения всё равно доступна

    try:
        from firebase_admin import messaging
        errors = []
        for device in devices:
            message = messaging.Message(
                token=device.push_token,
                notification=messaging.Notification(
                    title=notification.title, body=notification.body),
                data={str(k): str(v) for k, v in notification.payload.items()},
            )
            try:
                messaging.send(message)
            except messaging.UnregisteredError:
                # Токен недействителен — устройство деактивируется (ТЗ-02 п. 9.2)
                device.is_active = False
                device.save(update_fields=['is_active', 'updated_at'])
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        if errors:
            return False, '; '.join(errors)
        return True, ''
    except Exception as exc:  # noqa: BLE001
        logger.exception('Ошибка отправки push')
        return False, str(exc)
