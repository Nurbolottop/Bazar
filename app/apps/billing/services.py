"""Бизнес-логика начислений (ТЗ-02 раздел 4.1–4.2, 4.5–4.6)."""
import calendar
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import SystemSettings
from apps.core.money import ZERO, q2
from apps.core.services import audit
from apps.tenants.models import Tenant, TenantSpot

from .models import BillingRun, Charge


def effective_days(tenant: Tenant, s: SystemSettings | None = None) -> tuple[int, int]:
    """Эффективные billing_day и payment_day: индивидуальные либо из общих настроек."""
    s = s or SystemSettings.load()
    return (
        tenant.billing_day or s.default_billing_day,
        tenant.payment_day or s.default_payment_day,
    )


def clamp_day(year: int, month: int, day: int) -> datetime.date:
    """Короткие месяцы: отсутствующий день усекается до последнего дня месяца (ТЗ-02 п. 4.2)."""
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(day, last))


def billing_dates_for(tenant: Tenant, year: int, month: int,
                      s: SystemSettings | None = None) -> tuple[datetime.date, datetime.date]:
    """Пара (charged_date, due_date) для периода с защитой от схлопывания дат.

    Если после усечения к длине месяца даты совпали или поменялись местами
    (например, 29-е и 30-е в феврале), charged_date сдвигается назад так,
    чтобы остаться строго раньше due_date (ТЗ-02 п. 4.2).
    """
    billing_day, payment_day = effective_days(tenant, s)
    charged_date = clamp_day(year, month, billing_day)
    due_date = clamp_day(year, month, payment_day)
    if charged_date >= due_date:
        charged_date = due_date - datetime.timedelta(days=1)
    return charged_date, due_date


def next_due_date(tenant: Tenant, today: datetime.date | None = None,
                  s: SystemSettings | None = None) -> datetime.date:
    """Ближайший срок оплаты арендатора, строго позже сегодняшнего дня."""
    today = today or timezone.localdate()
    _, payment_day = effective_days(tenant, s)
    due = clamp_day(today.year, today.month, payment_day)
    if due <= today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        due = clamp_day(year, month, payment_day)
    return due


def refresh_charge_status(charge: Charge, today: datetime.date | None = None,
                          save: bool = True) -> Charge:
    """Пересчёт paid_amount и статуса начисления по активным распределениям."""
    from apps.payments.models import PaymentAllocation

    if charge.status == Charge.Status.CANCELLED:
        return charge
    today = today or timezone.localdate()
    paid = charge.allocations.filter(
        status=PaymentAllocation.Status.ACTIVE,
    ).aggregate(total=Sum('amount'))['total'] or ZERO
    charge.paid_amount = q2(paid)
    if charge.paid_amount >= charge.amount:
        charge.status = Charge.Status.PAID
    elif charge.due_date < today:
        charge.status = Charge.Status.OVERDUE
    elif charge.paid_amount > ZERO:
        charge.status = Charge.Status.PARTIAL
    else:
        charge.status = Charge.Status.UNPAID
    if save:
        charge.save(update_fields=['paid_amount', 'status', 'updated_at'])
    return charge


def run_billing(today: datetime.date | None = None, dry_run: bool = False,
                started_by=None) -> BillingRun:
    """Прогон начислений (ТЗ-02 п. 4.2).

    Идемпотентен: контроль по паре (tenant_spot, период). Каждый арендатор
    обрабатывается в своей транзакции — сбой на одном не прерывает прогон.
    """
    from apps.notifications import services as notification_services
    from apps.payments.services import consume_advance_for_charge, recalc_balance

    today = today or timezone.localdate()
    s = SystemSettings.load()

    created_count = 0
    total_amount = ZERO
    skipped: list[dict] = []
    errors: list[dict] = []
    preview: list[dict] = []

    tenants = Tenant.objects.filter(status=Tenant.Status.ACTIVE).order_by('id')
    for tenant in tenants:
        billing_day, _ = effective_days(tenant, s)
        # Прогон срабатывает в день, к которому усечён billing_day этого месяца
        if clamp_day(today.year, today.month, billing_day) != today:
            continue

        charged_date, due_date = billing_dates_for(tenant, today.year, today.month, s)
        spots = list(tenant.tenant_spots.filter(is_active=True).select_related('spot'))
        if not spots:
            skipped.append({'tenant': tenant.inn, 'reason': 'нет активных мест'})
            continue

        try:
            with transaction.atomic():
                for ts in spots:
                    exists = Charge.objects.filter(
                        tenant_spot=ts,
                        period_year=today.year, period_month=today.month,
                        source=Charge.Source.AUTO,
                    ).exclude(status=Charge.Status.CANCELLED).exists()
                    if exists:
                        skipped.append({
                            'tenant': tenant.inn, 'spot': ts.spot.code,
                            'reason': 'начисление за период уже существует'})
                        continue

                    amount = q2(ts.monthly_amount)
                    if dry_run:
                        preview.append({
                            'tenant': tenant.full_name, 'inn': tenant.inn,
                            'spot': ts.spot.code, 'amount': str(amount),
                            'due_date': due_date.isoformat()})
                        created_count += 1
                        total_amount += amount
                        continue

                    charge = Charge.objects.create(
                        tenant=tenant, tenant_spot=ts,
                        period_year=today.year, period_month=today.month,
                        amount=amount, charged_date=charged_date, due_date=due_date,
                        source=Charge.Source.AUTO)
                    # Аванс автоматически зачитывается в новое начисление (FR-PM-09)
                    consume_advance_for_charge(charge)
                    created_count += 1
                    total_amount += amount

                if not dry_run:
                    recalc_balance(tenant)
        except Exception as exc:  # noqa: BLE001 — сбой одного арендатора не прерывает прогон
            errors.append({'tenant': tenant.inn, 'error': str(exc)})
            continue

        if not dry_run:
            notification_services.notify_charges_created(tenant, due_date)

    run = BillingRun.objects.create(
        run_date=today, dry_run=dry_run,
        created_count=created_count, total_amount=q2(total_amount),
        skipped=skipped, errors=errors, started_by=started_by)
    if dry_run:
        run.preview = preview  # не сохраняется в базе, используется вызывающим кодом
    return run


def create_manual_charge(*, tenant: Tenant, amount: Decimal, comment: str,
                         actor=None, due_date: datetime.date | None = None,
                         charged_date: datetime.date | None = None,
                         source: str = Charge.Source.MANUAL) -> Charge:
    """Разовое начисление с произвольной суммой (FR-CH-04); также начальный долг (FR-TN-08)."""
    from apps.payments.services import consume_advance_for_charge, recalc_balance

    today = timezone.localdate()
    charged_date = charged_date or today
    due_date = due_date or next_due_date(tenant, charged_date)
    if charged_date >= due_date:
        charged_date = due_date - datetime.timedelta(days=1)
    amount = q2(amount)
    if amount <= ZERO:
        raise ValidationError('Сумма начисления должна быть больше нуля.')

    with transaction.atomic():
        charge = Charge.objects.create(
            tenant=tenant, tenant_spot=None,
            period_year=charged_date.year, period_month=charged_date.month,
            amount=amount, charged_date=charged_date, due_date=due_date,
            source=source, comment=comment)
        consume_advance_for_charge(charge)
        recalc_balance(tenant)
        audit(action='charge_create', model_name='Charge', object_id=charge.pk,
              actor=actor, new_value={'amount': str(amount), 'comment': comment})
    return charge


def cancel_charge(charge: Charge, *, reason: str, actor=None) -> Charge:
    """Отмена начисления (FR-CH-05): допустима только без распределённых платежей."""
    from apps.payments.models import PaymentAllocation
    from apps.payments.services import recalc_balance

    if not reason or not reason.strip():
        raise ValidationError('Укажите причину отмены начисления.')

    with transaction.atomic():
        charge = Charge.objects.select_for_update().get(pk=charge.pk)
        if charge.status == Charge.Status.CANCELLED:
            raise ValidationError('Начисление уже отменено.')
        has_allocations = charge.allocations.filter(
            status=PaymentAllocation.Status.ACTIVE).exists()
        if has_allocations:
            raise ValidationError(
                'По начислению есть распределённые платежи — сначала отмените платёж.')
        old_status = charge.status
        charge.status = Charge.Status.CANCELLED
        charge.cancelled_reason = reason
        charge.cancelled_by = actor if getattr(actor, 'pk', None) else None
        charge.save(update_fields=['status', 'cancelled_reason', 'cancelled_by', 'updated_at'])
        recalc_balance(charge.tenant)
        audit(action='charge_cancel', model_name='Charge', object_id=charge.pk,
              actor=actor, old_value={'status': old_status},
              new_value={'status': charge.status, 'reason': reason})
    return charge


def update_overdue_statuses(today: datetime.date | None = None) -> int:
    """Перевод начислений с истёкшим сроком в статус overdue (команда update_charge_statuses)."""
    today = today or timezone.localdate()
    updated = Charge.objects.filter(
        status__in=[Charge.Status.UNPAID, Charge.Status.PARTIAL],
        due_date__lt=today,
    ).update(status=Charge.Status.OVERDUE)
    return updated
