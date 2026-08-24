"""Контрольные сценарии начислений (ТЗ-02 п. 12.2)."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.billing.models import Charge
from apps.billing.services import (
    billing_dates_for, cancel_charge, create_manual_charge, run_billing,
    update_overdue_statuses,
)
from apps.core.models import SystemSettings
from apps.core.testutils import make_admin, make_spot, make_tenant, make_tenant_with_spot
from apps.payments.models import TenantBalance
from apps.payments.services import recalc_balance
from apps.tenants.models import Tenant, TenantSpot

MARCH_1 = datetime.date(2026, 3, 1)


class BillingRunTests(TestCase):
    def test_single_spot_charge(self):
        """Начисление за месяц по одному месту."""
        tenant, tenant_spot = make_tenant_with_spot('12000.00')
        run = run_billing(today=MARCH_1)
        self.assertEqual(run.created_count, 1)
        charge = Charge.objects.get(tenant=tenant)
        self.assertEqual(charge.amount, Decimal('12000.00'))
        self.assertEqual(charge.charged_date, MARCH_1)
        self.assertEqual(charge.due_date, datetime.date(2026, 3, 30))
        self.assertEqual(charge.status, Charge.Status.UNPAID)
        self.assertEqual(TenantBalance.objects.get(tenant=tenant).debt_amount,
                         Decimal('12000.00'))

    def test_multiple_spots_different_amounts(self):
        """Начисление по нескольким местам одного арендатора с разными суммами."""
        tenant, _ = make_tenant_with_spot('5000.00')
        spot2 = make_spot(code=f'S2-{tenant.inn}')
        TenantSpot.objects.create(
            tenant=tenant, spot=spot2, monthly_amount=Decimal('7500.00'),
            start_date=datetime.date(2026, 1, 1))
        run = run_billing(today=MARCH_1)
        self.assertEqual(run.created_count, 2)
        amounts = sorted(Charge.objects.filter(tenant=tenant).values_list('amount', flat=True))
        self.assertEqual(amounts, [Decimal('5000.00'), Decimal('7500.00')])
        self.assertEqual(TenantBalance.objects.get(tenant=tenant).debt_amount,
                         Decimal('12500.00'))

    def test_rerun_creates_no_duplicates(self):
        """Повторный запуск прогона за тот же месяц: дублей нет."""
        make_tenant_with_spot('12000.00')
        run_billing(today=MARCH_1)
        second = run_billing(today=MARCH_1)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(Charge.objects.count(), 1)
        self.assertTrue(any('уже существует' in s.get('reason', '') for s in second.skipped))

    def test_suspended_and_archived_skipped(self):
        """Арендаторы в статусах suspended и archived пропускаются."""
        tenant, _ = make_tenant_with_spot('1000.00')
        tenant.status = Tenant.Status.SUSPENDED
        tenant.save()
        run_billing(today=MARCH_1)
        self.assertEqual(Charge.objects.count(), 0)

    def test_dry_run_writes_nothing(self):
        """Предварительный просмотр прогона (FR-CH-07): расчёт есть, записей нет."""
        make_tenant_with_spot('12000.00')
        run = run_billing(today=MARCH_1, dry_run=True)
        self.assertEqual(run.created_count, 1)
        self.assertEqual(Charge.objects.count(), 0)
        self.assertEqual(len(run.preview), 1)

    def test_february_payment_day_truncated(self):
        """Срок оплаты 30-го числа в феврале: использован последний день месяца."""
        tenant, _ = make_tenant_with_spot('1000.00')
        feb1 = datetime.date(2026, 2, 1)
        run_billing(today=feb1)
        charge = Charge.objects.get(tenant=tenant)
        self.assertEqual(charge.due_date, datetime.date(2026, 2, 28))

    def test_february_dates_do_not_collapse(self):
        """Дата начисления 29-го, срок 30-го, февраль: даты не схлопнулись."""
        tenant, _ = make_tenant_with_spot('1000.00')
        tenant.billing_day = 29
        tenant.payment_day = 30
        tenant.save()
        # В феврале 2026 оба дня усекаются до 28-го; прогон срабатывает 28-го
        feb28 = datetime.date(2026, 2, 28)
        run = run_billing(today=feb28)
        self.assertEqual(run.created_count, 1)
        self.assertEqual(len(run.errors), 0)
        charge = Charge.objects.get(tenant=tenant)
        self.assertEqual(charge.due_date, datetime.date(2026, 2, 28))
        self.assertLess(charge.charged_date, charge.due_date)

    def test_charge_cancel(self):
        """Отмена начисления: долг уменьшился, запись сохранена с пометкой."""
        tenant, _ = make_tenant_with_spot('12000.00')
        run_billing(today=MARCH_1)
        charge = Charge.objects.get(tenant=tenant)
        admin = make_admin()
        cancel_charge(charge, reason='Ошибка в сумме', actor=admin)
        charge.refresh_from_db()
        self.assertEqual(charge.status, Charge.Status.CANCELLED)
        self.assertEqual(charge.cancelled_reason, 'Ошибка в сумме')
        self.assertEqual(Charge.objects.count(), 1)  # запись не удалена
        self.assertEqual(TenantBalance.objects.get(tenant=tenant).debt_amount, Decimal('0.00'))

    def test_cancel_requires_reason(self):
        tenant, _ = make_tenant_with_spot('12000.00')
        run_billing(today=MARCH_1)
        charge = Charge.objects.get(tenant=tenant)
        with self.assertRaises(ValidationError):
            cancel_charge(charge, reason='', actor=None)

    def test_overdue_status(self):
        """Начисление с истёкшим сроком переводится в overdue."""
        tenant, _ = make_tenant_with_spot('12000.00')
        run_billing(today=MARCH_1)
        updated = update_overdue_statuses(today=datetime.date(2026, 3, 31))
        self.assertEqual(updated, 1)
        charge = Charge.objects.get(tenant=tenant)
        self.assertEqual(charge.status, Charge.Status.OVERDUE)


class InitialDebtTests(TestCase):
    def test_initial_debt_included(self):
        """Импорт начальной задолженности: сумма вошла в долг арендатора."""
        tenant = make_tenant()
        charge = create_manual_charge(
            tenant=tenant, amount=Decimal('45000.00'),
            comment='Долг на начало работы Системы',
            charged_date=datetime.date(2026, 3, 10),
            source=Charge.Source.INITIAL)
        self.assertEqual(charge.source, Charge.Source.INITIAL)
        self.assertIsNone(charge.tenant_spot)
        self.assertLess(charge.charged_date, charge.due_date)
        self.assertEqual(TenantBalance.objects.get(tenant=tenant).debt_amount,
                         Decimal('45000.00'))


class BillingSettingsValidationTests(TestCase):
    def test_settings_reject_billing_after_payment(self):
        """Дата начисления позже срока оплаты либо равная ему: настройка отклонена."""
        s = SystemSettings.load()
        s.default_billing_day = 30
        s.default_payment_day = 30
        with self.assertRaises(ValidationError):
            s.full_clean()
        s.default_billing_day = 31
        with self.assertRaises(ValidationError):
            s.full_clean()

    def test_tenant_reject_billing_after_payment(self):
        tenant = make_tenant()
        tenant.billing_day = 30
        tenant.payment_day = 15
        with self.assertRaises(ValidationError):
            tenant.full_clean()

    def test_dates_helper_keeps_order(self):
        tenant = make_tenant(billing_day=29, payment_day=30)
        charged, due = billing_dates_for(tenant, 2026, 2)
        self.assertLess(charged, due)
