"""Вход арендатора по ИНН (ТЗ-00 п. 8.1, ТЗ-02 п. 7.1)."""
import datetime

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from apps.core.models import SystemSettings
from apps.tenants.models import Tenant

from .models import AuthToken, TenantLoginLog


class LoginRateLimited(Exception):
    """Превышен лимит попыток входа (429). Текст одинаков для любого ИНН."""

    message = 'Слишком много попыток входа. Повторите позже.'


class LoginFailed(Exception):
    def __init__(self, message: str = 'Вход невозможен. Проверьте ИНН.'):
        self.message = message
        super().__init__(message)


class PinRequired(Exception):
    """Включён PIN-код: требуется поле pin в запросе входа."""

    message = 'Требуется PIN-код.'


def _attempts_last_hour(ip: str | None, device_info: str) -> int:
    hour_ago = timezone.now() - datetime.timedelta(hours=1)
    qs = TenantLoginLog.objects.filter(created_at__gte=hour_ago)
    by_ip = qs.filter(ip=ip).count() if ip else 0
    by_device = qs.filter(user_agent=device_info).count() if device_info else 0
    return max(by_ip, by_device)


def tenant_login(*, inn: str, device_info: str = '', ip: str | None = None,
                 consent_accepted: bool = False, pin: str | None = None) -> tuple[Tenant, str]:
    """Вход по ИНН. Возвращает (арендатор, открытый ключ токена).

    Ограничение: не более 10 попыток с одного IP или устройства в час.
    Каждая попытка, успешная и нет, пишется в TenantLoginLog.
    """
    inn = (inn or '').strip()
    device_info = (device_info or '')[:512]

    if _attempts_last_hour(ip, device_info) >= settings.TENANT_LOGIN_MAX_ATTEMPTS_PER_HOUR:
        # Сообщение одинаково для существующего и несуществующего ИНН (ТЗ-02 п. 7.1)
        raise LoginRateLimited()

    tenant = Tenant.objects.filter(inn=inn).first()
    allowed = tenant is not None and tenant.status in (
        Tenant.Status.ACTIVE, Tenant.Status.SUSPENDED)

    system = SystemSettings.load()
    if allowed and system.pin_login_enabled and tenant.pin_hash:
        if pin is None:
            TenantLoginLog.objects.create(
                tenant=tenant, inn_attempted=inn, success=False,
                ip=ip, user_agent=device_info)
            raise PinRequired()
        if not check_password(pin, tenant.pin_hash):
            allowed = False

    TenantLoginLog.objects.create(
        tenant=tenant, inn_attempted=inn, success=allowed,
        ip=ip, user_agent=device_info)

    if not allowed:
        raise LoginFailed()

    # Согласие на обработку ПДн: дата и версия сохраняются при первом входе (FR-TN-10)
    if consent_accepted and not tenant.consent_accepted_at:
        tenant.consent_accepted_at = timezone.now()
        tenant.consent_version = system.consent_version
        tenant.save(update_fields=['consent_accepted_at', 'consent_version', 'updated_at'])

    _, raw_key = AuthToken.issue(tenant, device_info=device_info, ip=ip)
    return tenant, raw_key


def tenant_logout(*, tenant: Tenant, raw_key: str):
    """Отзыв токена и деактивация устройств при выходе."""
    token = AuthToken.objects.filter(
        tenant=tenant, key=AuthToken.hash_key(raw_key), revoked_at__isnull=True).first()
    if token:
        token.revoke()


def revoke_all_tokens(tenant: Tenant) -> int:
    """Отзыв всех токенов арендатора одним действием администратора (ТЗ-02 п. 7.1)."""
    count = 0
    for token in AuthToken.objects.filter(tenant=tenant, revoked_at__isnull=True):
        token.revoke()
        count += 1
    return count


def admin_login_blocked(username: str) -> bool:
    """Блокировка входа администратора на 15 минут после 5 неудачных попыток (ТЗ-02 п. 7.2)."""
    from .models import AdminLoginLog
    window_start = timezone.now() - datetime.timedelta(
        minutes=settings.ADMIN_LOGIN_LOCKOUT_MINUTES)
    recent = AdminLoginLog.objects.filter(
        username=username, created_at__gte=window_start).order_by('-created_at')
    failures = 0
    for attempt in recent:
        if attempt.success:
            break
        failures += 1
    return failures >= settings.ADMIN_LOGIN_MAX_ATTEMPTS


def record_admin_attempt(username: str, success: bool, ip: str | None):
    from .models import AdminLoginLog
    AdminLoginLog.objects.create(username=username[:150], success=success, ip=ip)


def set_pin(tenant: Tenant, pin: str):
    tenant.pin_hash = make_password(pin)
    tenant.save(update_fields=['pin_hash', 'updated_at'])
