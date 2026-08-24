"""Бизнес-логика заявок, платежей и долга (ТЗ-02 разделы 4.3–4.6).

Инварианты:
- финансовые записи не удаляются, только сторно;
- paid_amount начисления всегда равен сумме его активных распределений;
- сумма активных распределений платежа не превышает Payment.amount;
- TenantBalance — производная величина, пересчитывается в той же транзакции.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.billing.models import Charge
from apps.billing.services import next_due_date, refresh_charge_status
from apps.core.money import ZERO, q2
from apps.core.services import audit
from apps.tenants.models import Tenant

from .models import DebtAdjustment, Payment, PaymentAllocation, PaymentClaim, TenantBalance

# Максимальное превышение заявленной суммы над долгом (ТЗ-02 п. 4.3)
CLAIM_MAX_OVER_DEBT = Decimal('100000.00')


# ---------------------------------------------------------------------------
# Баланс
# ---------------------------------------------------------------------------

def _lock_balance(tenant: Tenant) -> TenantBalance:
    """Блокировка строки баланса (select_for_update) — против гонок двух администраторов."""
    TenantBalance.objects.get_or_create(tenant=tenant)
    return TenantBalance.objects.select_for_update().get(tenant=tenant)


def _active_allocation_sum(**filters) -> Decimal:
    total = PaymentAllocation.objects.filter(
        status=PaymentAllocation.Status.ACTIVE, **filters,
    ).aggregate(total=Sum('amount'))['total']
    return q2(total or ZERO)


def payment_free_amount(payment: Payment) -> Decimal:
    """Нераспределённый остаток платежа — источник аванса (FR-PM-09)."""
    return q2(payment.amount - _active_allocation_sum(payment=payment))


def adjustment_free_advance(adjustment: DebtAdjustment) -> Decimal:
    """Неизрасходованный аванс, образованный корректировкой в минус."""
    consumed = _active_allocation_sum(
        adjustment=adjustment, kind=PaymentAllocation.Kind.ADVANCE)
    return q2(adjustment.advance_excess - consumed)


def recalc_balance(tenant: Tenant) -> TenantBalance:
    """Полный пересчёт долга и аванса (формула ТЗ-02 п. 4.6).

    Долг = сумма непогашенных остатков начислений (включая не наступившие сроки
    и корректировки в плюс, оформленные начислениями source=adjustment).
    Аванс = нераспределённые остатки активных платежей + неизрасходованные
    излишки корректировок в минус. Долг не бывает отрицательным.
    """
    balance, _ = TenantBalance.objects.get_or_create(tenant=tenant)

    debt = ZERO
    for charge in tenant.charges.exclude(status=Charge.Status.CANCELLED):
        debt += charge.amount - charge.paid_amount

    advance = ZERO
    for payment in tenant.payments.filter(status=Payment.Status.ACTIVE):
        advance += payment_free_amount(payment)
    for adjustment in tenant.adjustments.filter(
            status=DebtAdjustment.Status.ACTIVE, advance_excess__gt=ZERO):
        advance += adjustment_free_advance(adjustment)

    if advance < ZERO:
        # Возможно только при рассинхронизации данных: возвращаем разницу в долг
        debt += -advance
        advance = ZERO

    balance.debt_amount = q2(max(debt, ZERO))
    balance.advance_amount = q2(advance)
    balance.save(update_fields=['debt_amount', 'advance_amount', 'updated_at'])
    return balance


def get_debt(tenant: Tenant) -> Decimal:
    balance = getattr(tenant, 'balance', None)
    if balance is None:
        with transaction.atomic():
            balance = recalc_balance(tenant)
    return balance.debt_amount


# ---------------------------------------------------------------------------
# Распределение
# ---------------------------------------------------------------------------

def _open_charges(tenant: Tenant):
    """Неоплаченные начисления от более ранних к более поздним по due_date (FR-PM-08)."""
    return (
        tenant.charges
        .exclude(status__in=[Charge.Status.CANCELLED, Charge.Status.PAID])
        .filter(paid_amount__lt=F('amount'))
        .order_by('due_date', 'id')
        .select_for_update()
    )


def _allocate(amount: Decimal, tenant: Tenant, *, payment: Payment | None = None,
              adjustment: DebtAdjustment | None = None,
              kind: str = PaymentAllocation.Kind.PAYMENT) -> Decimal:
    """Распределить сумму по открытым начислениям. Возвращает нераспределённый остаток."""
    remaining = q2(amount)
    today = timezone.localdate()
    for charge in _open_charges(tenant):
        if remaining <= ZERO:
            break
        part = min(remaining, charge.amount - charge.paid_amount)
        if part <= ZERO:
            continue
        PaymentAllocation.objects.create(
            payment=payment, adjustment=adjustment, charge=charge,
            amount=q2(part), kind=kind)
        refresh_charge_status(charge, today)
        remaining = q2(remaining - part)
    return remaining


def consume_advance_for_charge(charge: Charge) -> Decimal:
    """Зачёт имеющегося аванса в новое начисление (FR-PM-09, прогон п. 4.2).

    Аванс привязывается к источнику: сначала нераспределённые остатки платежей
    (от ранних к поздним), затем излишки корректировок. Возвращает зачтённую сумму.
    """
    need = q2(charge.amount - charge.paid_amount)
    consumed_total = ZERO
    if need <= ZERO:
        return consumed_total
    today = timezone.localdate()

    for payment in charge.tenant.payments.filter(
            status=Payment.Status.ACTIVE).order_by('paid_at', 'id'):
        if need <= ZERO:
            break
        free = payment_free_amount(payment)
        if free <= ZERO:
            continue
        part = min(free, need)
        PaymentAllocation.objects.create(
            payment=payment, charge=charge, amount=q2(part),
            kind=PaymentAllocation.Kind.ADVANCE)
        need = q2(need - part)
        consumed_total = q2(consumed_total + part)

    for adjustment in charge.tenant.adjustments.filter(
            status=DebtAdjustment.Status.ACTIVE,
            advance_excess__gt=ZERO).order_by('created_at', 'id'):
        if need <= ZERO:
            break
        free = adjustment_free_advance(adjustment)
        if free <= ZERO:
            continue
        part = min(free, need)
        PaymentAllocation.objects.create(
            adjustment=adjustment, charge=charge, amount=q2(part),
            kind=PaymentAllocation.Kind.ADVANCE)
        need = q2(need - part)
        consumed_total = q2(consumed_total + part)

    if consumed_total > ZERO:
        refresh_charge_status(charge, today)
    return consumed_total


# ---------------------------------------------------------------------------
# Заявки об оплате
# ---------------------------------------------------------------------------

def create_claim(*, tenant: Tenant, declared_amount: Decimal, receipt_image,
                 idempotency_key: str, comment: str = '',
                 device_info: str = '') -> PaymentClaim:
    """Подача заявки об оплате арендатором (ТЗ-02 п. 4.3). Долг не меняется."""
    if tenant.status == Tenant.Status.SUSPENDED:
        raise PermissionError('Приостановленный арендатор не может подавать заявки.')
    if tenant.status != Tenant.Status.ACTIVE:
        raise PermissionError('Подача заявки недоступна.')

    existing = PaymentClaim.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing  # идемпотентность: вернуть существующую, не создавая новую

    declared_amount = q2(declared_amount)
    if declared_amount < Decimal('1.00'):
        raise ValidationError('Сумма заявки должна быть не менее 1 сома.')
    max_amount = get_debt(tenant) + CLAIM_MAX_OVER_DEBT
    if declared_amount > max_amount:
        raise ValidationError(
            f'Сумма заявки не может превышать долг более чем на {CLAIM_MAX_OVER_DEBT} сом.')

    claim = PaymentClaim.objects.create(
        tenant=tenant, declared_amount=declared_amount,
        receipt_image=receipt_image, comment=comment,
        idempotency_key=idempotency_key, device_info=device_info[:255])
    return claim


def confirm_claim(*, claim: PaymentClaim, actor, amount: Decimal | None = None) -> Payment:
    """Подтверждение заявки (FR-PM-04, FR-PM-06). Вся операция — одна транзакция."""
    from apps.notifications import services as notification_services

    with transaction.atomic():
        claim = PaymentClaim.objects.select_for_update().get(pk=claim.pk)
        if claim.status != PaymentClaim.Status.PENDING:
            raise ClaimAlreadyProcessed(claim)
        _lock_balance(claim.tenant)

        accepted = q2(amount if amount is not None else claim.declared_amount)
        if accepted <= ZERO:
            raise ValidationError('Сумма платежа должна быть больше нуля.')

        payment = Payment.objects.create(
            tenant=claim.tenant, amount=accepted, paid_at=timezone.now(),
            source=Payment.Source.CLAIM, claim=claim,
            created_by=actor if getattr(actor, 'pk', None) else None)
        leftover = _allocate(accepted, claim.tenant, payment=payment)
        # Остаток, не покрывший ни одно начисление, остаётся авансом (FR-PM-09):
        # он виден как нераспределённая часть платежа и учитывается recalc_balance.

        claim.status = PaymentClaim.Status.CONFIRMED
        claim.reviewed_at = timezone.now()
        claim.reviewed_by = actor if getattr(actor, 'pk', None) else None
        claim.save(update_fields=['status', 'reviewed_at', 'reviewed_by', 'updated_at'])

        balance = recalc_balance(claim.tenant)
        audit(action='claim_confirm', model_name='PaymentClaim', object_id=claim.pk,
              actor=actor,
              old_value={'declared_amount': str(claim.declared_amount)},
              new_value={'accepted_amount': str(accepted), 'payment_id': payment.pk})

    notification_services.notify_claim_confirmed(claim.tenant, accepted, balance.debt_amount)
    return payment


def reject_claim(*, claim: PaymentClaim, actor, reason: str) -> PaymentClaim:
    """Отклонение заявки (FR-PM-05): долг не меняется, причина обязательна."""
    from apps.notifications import services as notification_services

    if not reason or not reason.strip():
        raise ValidationError('Укажите причину отклонения.')

    with transaction.atomic():
        claim = PaymentClaim.objects.select_for_update().get(pk=claim.pk)
        if claim.status != PaymentClaim.Status.PENDING:
            raise ClaimAlreadyProcessed(claim)
        claim.status = PaymentClaim.Status.REJECTED
        claim.reject_reason = reason
        claim.reviewed_at = timezone.now()
        claim.reviewed_by = actor if getattr(actor, 'pk', None) else None
        claim.save(update_fields=[
            'status', 'reject_reason', 'reviewed_at', 'reviewed_by', 'updated_at'])
        audit(action='claim_reject', model_name='PaymentClaim', object_id=claim.pk,
              actor=actor, new_value={'reason': reason})

    notification_services.notify_claim_rejected(claim.tenant, reason)
    return claim


def withdraw_claim(*, claim: PaymentClaim, tenant: Tenant) -> PaymentClaim:
    """Отзыв собственной заявки, пока она не обработана (FR-PM-14)."""
    with transaction.atomic():
        claim = PaymentClaim.objects.select_for_update().get(pk=claim.pk)
        if claim.tenant_id != tenant.pk:
            raise PermissionError('Чужая заявка.')
        if claim.status != PaymentClaim.Status.PENDING:
            raise ClaimAlreadyProcessed(claim)
        claim.status = PaymentClaim.Status.WITHDRAWN
        claim.reviewed_at = timezone.now()
        claim.save(update_fields=['status', 'reviewed_at', 'updated_at'])
        audit(action='claim_withdraw', model_name='PaymentClaim', object_id=claim.pk,
              actor_type='tenant', new_value={'status': claim.status})
    return claim


class ClaimAlreadyProcessed(Exception):
    """Заявка уже обработана другим администратором (ТЗ-02 п. 5.3) либо отозвана."""

    def __init__(self, claim: PaymentClaim):
        self.claim = claim
        super().__init__(f'Заявка уже обработана: статус «{claim.get_status_display()}».')


# ---------------------------------------------------------------------------
# Платежи
# ---------------------------------------------------------------------------

def create_manual_payment(*, tenant: Tenant, amount: Decimal, actor,
                          paid_at=None, comment: str = '') -> Payment:
    """Платёж, внесённый администратором напрямую (FR-PM-10)."""
    amount = q2(amount)
    if amount <= ZERO:
        raise ValidationError('Сумма платежа должна быть больше нуля.')

    with transaction.atomic():
        _lock_balance(tenant)
        payment = Payment.objects.create(
            tenant=tenant, amount=amount, paid_at=paid_at or timezone.now(),
            source=Payment.Source.MANUAL, comment=comment,
            created_by=actor if getattr(actor, 'pk', None) else None)
        _allocate(amount, tenant, payment=payment)
        recalc_balance(tenant)
        audit(action='payment_create_manual', model_name='Payment', object_id=payment.pk,
              actor=actor, new_value={'amount': str(amount)})
    return payment


def reverse_payment(*, payment: Payment, actor, reason: str) -> Payment:
    """Сторно платежа (FR-PM-11, ТЗ-02 п. 4.5).

    Откатываются и прямые распределения, и записи kind=advance, созданные из
    переплаты этого платежа, — долг по соответствующим начислениям восстанавливается.
    """
    if not reason or not reason.strip():
        raise ValidationError('Укажите причину отмены платежа.')

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status == Payment.Status.REVERSED:
            raise ValidationError('Платёж уже отменён.')
        _lock_balance(payment.tenant)

        now = timezone.now()
        today = timezone.localdate()
        allocations = list(payment.allocations.filter(
            status=PaymentAllocation.Status.ACTIVE).select_related('charge'))
        for allocation in allocations:
            allocation.status = PaymentAllocation.Status.REVERSED
            allocation.reversed_at = now
            allocation.reversed_by = actor if getattr(actor, 'pk', None) else None
            allocation.save(update_fields=['status', 'reversed_at', 'reversed_by', 'updated_at'])
            refresh_charge_status(allocation.charge, today)

        payment.status = Payment.Status.REVERSED
        payment.reversed_reason = reason
        payment.reversed_by = actor if getattr(actor, 'pk', None) else None
        payment.save(update_fields=['status', 'reversed_reason', 'reversed_by', 'updated_at'])

        recalc_balance(payment.tenant)
        audit(action='payment_reverse', model_name='Payment', object_id=payment.pk,
              actor=actor, old_value={'status': 'active'},
              new_value={'status': 'reversed', 'reason': reason})
    return payment


# ---------------------------------------------------------------------------
# Корректировки долга
# ---------------------------------------------------------------------------

def adjust_debt(*, tenant: Tenant, amount: Decimal, reason: str, actor) -> DebtAdjustment:
    """Ручная корректировка долга (FR-PM-15).

    Плюс — долг увеличивается (оформляется начислением source=adjustment,
    которое гасится будущими платежами наравне с остальными).
    Минус — погашаются открытые начисления от ранних к поздним; излишек
    зачисляется в аванс, итоговый долг не бывает отрицательным.
    """
    from apps.billing.services import create_manual_charge

    amount = q2(amount)
    if amount == ZERO:
        raise ValidationError('Сумма корректировки не может быть нулевой.')
    if not reason or not reason.strip():
        raise ValidationError('Укажите причину корректировки.')

    with transaction.atomic():
        _lock_balance(tenant)
        adjustment = DebtAdjustment.objects.create(
            tenant=tenant, amount=amount, reason=reason,
            created_by=actor if getattr(actor, 'pk', None) else None)

        if amount > ZERO:
            charge = create_manual_charge(
                tenant=tenant, amount=amount,
                comment=f'Корректировка долга: {reason}', actor=actor,
                source=Charge.Source.ADJUSTMENT)
            adjustment.charge = charge
            adjustment.save(update_fields=['charge', 'updated_at'])
        else:
            leftover = _allocate(
                -amount, tenant, adjustment=adjustment,
                kind=PaymentAllocation.Kind.ADJUSTMENT)
            if leftover > ZERO:
                adjustment.advance_excess = leftover
                adjustment.save(update_fields=['advance_excess', 'updated_at'])

        recalc_balance(tenant)
        audit(action='debt_adjust', model_name='DebtAdjustment', object_id=adjustment.pk,
              actor=actor, new_value={'amount': str(amount), 'reason': reason})
    return adjustment


def reverse_adjustment(*, adjustment: DebtAdjustment, actor, reason: str) -> DebtAdjustment:
    """Отмена корректировки: откатываются все её распределения, долг пересчитывается."""
    from apps.billing.services import cancel_charge

    if not reason or not reason.strip():
        raise ValidationError('Укажите причину отмены корректировки.')

    with transaction.atomic():
        adjustment = DebtAdjustment.objects.select_for_update().get(pk=adjustment.pk)
        if adjustment.status == DebtAdjustment.Status.REVERSED:
            raise ValidationError('Корректировка уже отменена.')
        _lock_balance(adjustment.tenant)

        now = timezone.now()
        today = timezone.localdate()
        for allocation in adjustment.allocations.filter(
                status=PaymentAllocation.Status.ACTIVE).select_related('charge'):
            allocation.status = PaymentAllocation.Status.REVERSED
            allocation.reversed_at = now
            allocation.reversed_by = actor if getattr(actor, 'pk', None) else None
            allocation.save(update_fields=['status', 'reversed_at', 'reversed_by', 'updated_at'])
            refresh_charge_status(allocation.charge, today)

        if adjustment.charge and adjustment.charge.status != Charge.Status.CANCELLED:
            cancel_charge(adjustment.charge, reason=f'Отмена корректировки: {reason}', actor=actor)

        adjustment.status = DebtAdjustment.Status.REVERSED
        adjustment.save(update_fields=['status', 'updated_at'])
        recalc_balance(adjustment.tenant)
        audit(action='debt_adjust_reverse', model_name='DebtAdjustment',
              object_id=adjustment.pk, actor=actor, new_value={'reason': reason})
    return adjustment
