"""Тесты напоминаний и шаблонов (FR-NT-01, FR-NT-07, FR-NT-10)."""
import datetime

from django.test import TestCase

from apps.billing.services import run_billing, update_overdue_statuses
from apps.core.testutils import make_tenant_with_spot
from apps.notifications.models import Notification, NotificationCode
from apps.notifications.services import send_reminders
from apps.tenants.models import Tenant


class ReminderTests(TestCase):
    def setUp(self):
        self.tenant, _ = make_tenant_with_spot('12000.00')
        run_billing(today=datetime.date(2026, 3, 1))
        # уведомление о создании начисления уже в ленте
        Notification.objects.all().delete()

    def test_reminder_schedule_for_march(self):
        """Начисление 1-го со сроком 30-го: напоминания отправлены 20, 25, 27 и 30-го."""
        expectations = {
            datetime.date(2026, 3, 20): NotificationCode.REMINDER_10,
            datetime.date(2026, 3, 25): NotificationCode.REMINDER_5,
            datetime.date(2026, 3, 27): NotificationCode.REMINDER_3,
            datetime.date(2026, 3, 30): NotificationCode.REMINDER_0,
        }
        for day, code in expectations.items():
            queued = send_reminders(today=day)
            self.assertEqual(queued, 1, f'{day}: ожидалось одно напоминание')
            self.assertTrue(
                Notification.objects.filter(tenant=self.tenant, code=code).exists(),
                f'{day}: нет уведомления {code}')

        # в «пустой» день напоминаний нет
        self.assertEqual(send_reminders(today=datetime.date(2026, 3, 22)), 0)

    def test_no_duplicate_reminder_same_day(self):
        """Не более одного напоминания одного типа в сутки (FR-NT-10)."""
        day = datetime.date(2026, 3, 20)
        self.assertEqual(send_reminders(today=day), 1)
        self.assertEqual(send_reminders(today=day), 0)

    def test_no_reminder_after_payment(self):
        """Напоминание отправляется только при неоплаченном начислении (FR-NT-01)."""
        from decimal import Decimal
        from apps.core.testutils import make_admin
        from apps.payments.services import create_manual_payment
        create_manual_payment(
            tenant=self.tenant, amount=Decimal('12000.00'), actor=make_admin())
        self.assertEqual(send_reminders(today=datetime.date(2026, 3, 20)), 0)

    def test_overdue_notification(self):
        update_overdue_statuses(today=datetime.date(2026, 3, 31))
        queued = send_reminders(today=datetime.date(2026, 3, 31))
        self.assertEqual(queued, 1)
        self.assertTrue(Notification.objects.filter(
            tenant=self.tenant, code=NotificationCode.OVERDUE).exists())

    def test_notification_language(self):
        """Уведомление приходит на языке арендатора (FR-NT-09)."""
        self.tenant.language = Tenant.Language.KY
        self.tenant.save()
        send_reminders(today=datetime.date(2026, 3, 20))
        notification = Notification.objects.get(tenant=self.tenant)
        self.assertIn('күн', notification.body)
