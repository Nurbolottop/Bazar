"""Контрольные сценарии заявок, платежей, аванса и корректировок (ТЗ-02 п. 12.2)."""
import datetime
import tempfile
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.billing.models import Charge
from apps.billing.services import run_billing
from apps.core.models import AuditLog
from apps.core.testutils import make_admin, make_tenant_with_spot, png_upload
from apps.notifications.models import Notification, NotificationCode
from apps.payments.models import (
    DebtAdjustment, Payment, PaymentAllocation, PaymentClaim, TenantBalance,
)
from apps.payments.services import (
    ClaimAlreadyProcessed, adjust_debt, confirm_claim, create_claim,
    create_manual_payment, get_debt, recalc_balance, reject_claim,
    reverse_payment, withdraw_claim,
)

def first_day_of_future_month(offset: int = 1) -> datetime.date:
    """Первое число будущего месяца: сроки оплаты в тестах не должны быть в прошлом."""
    from django.utils import timezone
    today = timezone.localdate()
    month = today.month - 1 + offset
    return datetime.date(today.year + month // 12, month % 12 + 1, 1)


MARCH_1 = first_day_of_future_month(1)   # период первого начисления
APRIL_1 = first_day_of_future_month(2)   # период следующего начисления

TMP_MEDIA = tempfile.mkdtemp(prefix='bazar_test_media_')


def balance_of(tenant) -> TenantBalance:
    return TenantBalance.objects.get(tenant=tenant)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ClaimFlowTests(TestCase):
    def setUp(self):
        self.tenant, self.tenant_spot = make_tenant_with_spot('12000.00')
        run_billing(today=MARCH_1)
        self.admin = make_admin()

    def _claim(self, amount, key='key-1'):
        return create_claim(
            tenant=self.tenant, declared_amount=Decimal(amount),
            receipt_image=png_upload(), idempotency_key=key)

    def test_full_payment(self):
        """Полная оплата: заявка, подтверждение, обнуление долга."""
        claim = self._claim('12000.00')
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('12000.00'),
                         'Заявка не меняет долг (FR-PM-02)')
        confirm_claim(claim=claim, actor=self.admin)
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('0.00'))
        charge = Charge.objects.get(tenant=self.tenant)
        self.assertEqual(charge.status, Charge.Status.PAID)
        self.assertEqual(
            Notification.objects.filter(
                tenant=self.tenant, code=NotificationCode.CLAIM_CONFIRMED).count(), 1)

    def test_partial_payment(self):
        """Частичная оплата: 5 000 из 12 000, статус partial, остаток 7 000."""
        claim = self._claim('5000.00')
        confirm_claim(claim=claim, actor=self.admin)
        charge = Charge.objects.get(tenant=self.tenant)
        self.assertEqual(charge.status, Charge.Status.PARTIAL)
        self.assertEqual(charge.paid_amount, Decimal('5000.00'))
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('7000.00'))
        notification = Notification.objects.get(
            tenant=self.tenant, code=NotificationCode.CLAIM_CONFIRMED_PARTIAL)
        self.assertIn('7 000', notification.body)

    def test_two_partial_payments_close_charge(self):
        """Две последовательные частичные оплаты, закрывающие начисление полностью."""
        confirm_claim(claim=self._claim('5000.00', 'k1'), actor=self.admin)
        confirm_claim(claim=self._claim('7000.00', 'k2'), actor=self.admin)
        charge = Charge.objects.get(tenant=self.tenant)
        self.assertEqual(charge.status, Charge.Status.PAID)
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('0.00'))

    def test_overpayment_goes_to_advance_and_offsets_next_month(self):
        """Переплата: излишек уходит в аванс и зачитывается в следующем месяце."""
        confirm_claim(claim=self._claim('15000.00'), actor=self.admin)
        balance = balance_of(self.tenant)
        self.assertEqual(balance.debt_amount, Decimal('0.00'))
        self.assertEqual(balance.advance_amount, Decimal('3000.00'))

        run_billing(today=APRIL_1)
        balance = balance_of(self.tenant)
        self.assertEqual(balance.advance_amount, Decimal('0.00'))
        self.assertEqual(balance.debt_amount, Decimal('9000.00'))
        april_charge = Charge.objects.get(tenant=self.tenant, period_month=APRIL_1.month, period_year=APRIL_1.year)
        self.assertEqual(april_charge.paid_amount, Decimal('3000.00'))
        advance_allocation = april_charge.allocations.get()
        self.assertEqual(advance_allocation.kind, PaymentAllocation.Kind.ADVANCE)
        self.assertIsNotNone(advance_allocation.payment,
                             'Зачёт аванса ссылается на исходный платёж-переплату')

    def test_oldest_charge_paid_first(self):
        """Оплата при нескольких неоплаченных начислениях: гашение от раннего к позднему."""
        run_billing(today=APRIL_1)  # второе начисление
        charges = list(Charge.objects.filter(tenant=self.tenant).order_by('due_date'))
        self.assertEqual(len(charges), 2)
        confirm_claim(claim=self._claim('13000.00'), actor=self.admin)
        march, april = [Charge.objects.get(pk=c.pk) for c in charges]
        self.assertEqual(march.status, Charge.Status.PAID)
        self.assertEqual(april.paid_amount, Decimal('1000.00'))
        self.assertEqual(april.status, Charge.Status.PARTIAL)

    def test_reject_claim(self):
        """Отклонение заявки: долг не изменился, уведомление доставлено, повторная подача возможна."""
        claim = self._claim('12000.00')
        reject_claim(claim=claim, actor=self.admin, reason='Чек нечитаемый')
        claim.refresh_from_db()
        self.assertEqual(claim.status, PaymentClaim.Status.REJECTED)
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('12000.00'))
        notification = Notification.objects.get(
            tenant=self.tenant, code=NotificationCode.CLAIM_REJECTED)
        self.assertIn('Чек нечитаемый', notification.body)
        # повторная подача возможна
        second = self._claim('12000.00', key='key-2')
        self.assertEqual(second.status, PaymentClaim.Status.PENDING)

    def test_confirm_with_corrected_amount_keeps_original_in_audit(self):
        """Подтверждение с исправленной суммой: исходное значение сохранено в журнале."""
        claim = self._claim('12000.00')
        confirm_claim(claim=claim, actor=self.admin, amount=Decimal('11500.00'))
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('500.00'))
        entry = AuditLog.objects.get(action='claim_confirm', object_id=str(claim.pk))
        self.assertEqual(entry.old_value['declared_amount'], '12000.00')
        self.assertEqual(entry.new_value['accepted_amount'], '11500.00')

    def test_reverse_payment_restores_debt(self):
        """Отмена подтверждённого платежа: долг восстановлен, статусы пересчитаны."""
        claim = self._claim('12000.00')
        payment = confirm_claim(claim=claim, actor=self.admin)
        reverse_payment(payment=payment, actor=self.admin, reason='Платёж не найден')
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REVERSED)
        charge = Charge.objects.get(tenant=self.tenant)
        self.assertEqual(charge.paid_amount, Decimal('0.00'))
        self.assertIn(charge.status, [Charge.Status.UNPAID, Charge.Status.OVERDUE])
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('12000.00'))
        # распределения не удалены, а сторнированы
        allocation = PaymentAllocation.objects.get(payment=payment)
        self.assertEqual(allocation.status, PaymentAllocation.Status.REVERSED)

    def test_reverse_payment_rolls_back_advance(self):
        """Отмена платежа с переплатой откатывает и зачтённый аванс."""
        confirm_claim(claim=self._claim('15000.00'), actor=self.admin)
        payment = Payment.objects.get(tenant=self.tenant)
        run_billing(today=APRIL_1)  # аванс 3000 зачтён в апрельское начисление
        reverse_payment(payment=payment, actor=self.admin, reason='Сторно')
        balance = balance_of(self.tenant)
        self.assertEqual(balance.advance_amount, Decimal('0.00'))
        # долг: март 12000 + апрель 12000
        self.assertEqual(balance.debt_amount, Decimal('24000.00'))
        april_charge = Charge.objects.get(tenant=self.tenant, period_month=APRIL_1.month, period_year=APRIL_1.year)
        self.assertEqual(april_charge.paid_amount, Decimal('0.00'))

    def test_idempotency_key_returns_same_claim(self):
        """Повторная отправка заявки с тем же ключом: вторая заявка не создана."""
        first = self._claim('5000.00', key='same-key')
        second = self._claim('5000.00', key='same-key')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentClaim.objects.count(), 1)

    def test_double_confirm_creates_single_payment(self):
        """Одновременное подтверждение одной заявки двумя администраторами: платёж один."""
        claim = self._claim('12000.00')
        confirm_claim(claim=claim, actor=self.admin)
        admin2 = make_admin('admin2')
        with self.assertRaises(ClaimAlreadyProcessed):
            confirm_claim(claim=claim, actor=admin2)
        self.assertEqual(Payment.objects.count(), 1)

    def test_withdraw_claim(self):
        """Отзыв заявки арендатором: статус withdrawn, администратор не может подтвердить."""
        claim = self._claim('12000.00')
        withdraw_claim(claim=claim, tenant=self.tenant)
        claim.refresh_from_db()
        self.assertEqual(claim.status, PaymentClaim.Status.WITHDRAWN)
        with self.assertRaises(ClaimAlreadyProcessed):
            confirm_claim(claim=claim, actor=self.admin)
        self.assertEqual(Payment.objects.count(), 0)

    def test_claim_amount_bounds(self):
        with self.assertRaises(ValidationError):
            self._claim('0.50')
        with self.assertRaises(ValidationError):
            self._claim('200000.00')  # долг 12000 + 100000 < 200000

    def test_manual_payment_without_claim(self):
        """Администратор вносит платёж напрямую (FR-PM-10)."""
        payment = create_manual_payment(
            tenant=self.tenant, amount=Decimal('12000.00'), actor=self.admin)
        self.assertEqual(payment.source, Payment.Source.MANUAL)
        self.assertIsNone(payment.claim)
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('0.00'))

    def test_payment_without_charges_goes_to_advance(self):
        """Оплата при отсутствии начислений: вся сумма в аванс."""
        confirm_claim(claim=self._claim('12000.00'), actor=self.admin)
        create_manual_payment(
            tenant=self.tenant, amount=Decimal('500.00'), actor=self.admin)
        balance = balance_of(self.tenant)
        self.assertEqual(balance.debt_amount, Decimal('0.00'))
        self.assertEqual(balance.advance_amount, Decimal('500.00'))


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class DebtAdjustmentTests(TestCase):
    def setUp(self):
        self.tenant, _ = make_tenant_with_spot('12000.00')
        run_billing(today=MARCH_1)
        self.admin = make_admin()

    def test_adjustment_plus_and_minus(self):
        """Корректировка в плюс и в минус: долг изменился, запись видна с причиной."""
        adjust_debt(tenant=self.tenant, amount=Decimal('3000.00'),
                    reason='Доначисление за электричество', actor=self.admin)
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('15000.00'))

        adjustment = adjust_debt(tenant=self.tenant, amount=Decimal('-5000.00'),
                                 reason='Скидка по договорённости', actor=self.admin)
        self.assertEqual(balance_of(self.tenant).debt_amount, Decimal('10000.00'))
        self.assertEqual(adjustment.reason, 'Скидка по договорённости')
        self.assertEqual(DebtAdjustment.objects.count(), 2)

    def test_negative_adjustment_exceeding_debt(self):
        """Корректировка в минус больше долга: долг обнулён, разница в аванс."""
        adjustment = adjust_debt(tenant=self.tenant, amount=Decimal('-15000.00'),
                                 reason='Компенсация', actor=self.admin)
        balance = balance_of(self.tenant)
        self.assertEqual(balance.debt_amount, Decimal('0.00'))
        self.assertEqual(balance.advance_amount, Decimal('3000.00'))
        self.assertEqual(adjustment.advance_excess, Decimal('3000.00'))

    def test_adjustment_requires_reason(self):
        with self.assertRaises(ValidationError):
            adjust_debt(tenant=self.tenant, amount=Decimal('-100.00'),
                        reason='', actor=self.admin)
