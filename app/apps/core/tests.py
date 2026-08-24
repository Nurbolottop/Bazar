"""Тесты веб-панели: доступ, вход с блокировкой, ключевые страницы и действия."""
import datetime
import io
import tempfile
from decimal import Decimal

from django.test import Client, TestCase, override_settings

from apps.billing.services import run_billing
from apps.core.testutils import make_admin, make_tenant_with_spot, png_upload
from apps.payments.models import Payment, PaymentClaim
from apps.payments.services import create_claim

TMP_MEDIA = tempfile.mkdtemp(prefix='bazar_test_media_')


def future_month(offset=1):
    from django.utils import timezone
    today = timezone.localdate()
    month = today.month - 1 + offset
    return datetime.date(today.year + month // 12, month % 12 + 1, 1)


class PanelAccessTests(TestCase):
    def test_anonymous_redirected_to_login(self):
        response = Client().get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_admin_login_and_dashboard(self):
        make_admin('chief')
        client = Client()
        response = client.post('/login/', {'username': 'chief', 'password': 'x' * 12})
        self.assertRedirects(response, '/')
        response = client.get('/')
        self.assertContains(response, 'Общая сумма долга')

    def test_login_lockout_after_5_failures(self):
        """Блокировка на 15 минут после 5 неудачных попыток (ТЗ-02 п. 7.2)."""
        make_admin('chief')
        client = Client()
        for _ in range(5):
            client.post('/login/', {'username': 'chief', 'password': 'wrong'})
        response = client.post('/login/', {'username': 'chief', 'password': 'x' * 12})
        self.assertContains(response, 'заблокирована')

    def test_non_staff_forbidden(self):
        from django.contrib.auth.models import User
        User.objects.create_user(username='plain', password='y' * 12, is_staff=False)
        client = Client()
        client.post('/login/', {'username': 'plain', 'password': 'y' * 12})
        response = client.get('/')
        self.assertIn(response.status_code, (302, 403))


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class PanelClaimFlowTests(TestCase):
    def setUp(self):
        self.admin = make_admin('chief')
        self.client_web = Client()
        self.client_web.post('/login/', {'username': 'chief', 'password': 'x' * 12})
        self.tenant, _ = make_tenant_with_spot('12000.00')
        run_billing(today=future_month(1))
        self.claim = create_claim(
            tenant=self.tenant, declared_amount=Decimal('12000.00'),
            receipt_image=png_upload(), idempotency_key='panel-claim-1')

    def test_claims_list_shows_pending(self):
        response = self.client_web.get('/claims/')
        self.assertContains(response, self.tenant.full_name)

    def test_confirm_from_panel(self):
        response = self.client_web.post(f'/claims/{self.claim.pk}/confirm/', {'amount': ''})
        self.assertEqual(response.status_code, 302)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, PaymentClaim.Status.CONFIRMED)
        self.assertEqual(self.claim.reviewed_by, self.admin)
        payment = Payment.objects.get()
        self.assertEqual(payment.amount, Decimal('12000.00'))

    def test_reject_from_panel_requires_reason(self):
        self.client_web.post(f'/claims/{self.claim.pk}/reject/', {'reason': ''})
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, PaymentClaim.Status.PENDING)
        self.client_web.post(f'/claims/{self.claim.pk}/reject/', {'reason': 'Чек нечитаемый'})
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, PaymentClaim.Status.REJECTED)

    def test_double_confirm_warning(self):
        self.client_web.post(f'/claims/{self.claim.pk}/confirm/', {'amount': ''})
        response = self.client_web.post(
            f'/claims/{self.claim.pk}/confirm/', {'amount': ''}, follow=True)
        self.assertContains(response, 'уже обработана')
        self.assertEqual(Payment.objects.count(), 1)

    def test_mass_amounts_save(self):
        tenant_spot = self.tenant.tenant_spots.get()
        response = self.client_web.post('/amounts/', {
            f'amount_{tenant_spot.pk}': '13500.00'})
        self.assertEqual(response.status_code, 302)
        tenant_spot.refresh_from_db()
        self.assertEqual(tenant_spot.monthly_amount, Decimal('13500.00'))

    def test_dashboard_counters(self):
        response = self.client_web.get('/')
        self.assertContains(response, '12 000')


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ImportTests(TestCase):
    HEADER = ['Корпус', 'Код места', 'Тип', 'Площадь', 'ФИО', 'ИНН', 'Паспорт',
              'Телефон', 'Сумма', 'Дата начисления', 'Срок оплаты', 'Долг']

    def _xlsx(self, rows):
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(self.HEADER)
        for row in rows:
            sheet.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return buffer

    def test_dry_run_reports_errors_without_writing(self):
        from apps.tenants.import_service import run_import
        from apps.tenants.models import Tenant
        file = self._xlsx([
            ['А', 'А-01', 'контейнер', '12', 'Иванов Иван', '12345678901234',
             '', '', '12000', '', '', '5000'],
            ['А', 'А-02', 'контейнер', '', '', 'abc', '', '', '', '', '', ''],
        ])
        report = run_import(file, dry_run=True)
        self.assertFalse(report.ok)
        fields = {e['field'] for e in report.errors}
        self.assertIn('ФИО', fields)
        self.assertIn('ИНН', fields)
        self.assertIn('сумма', fields)
        self.assertEqual(Tenant.objects.count(), 0, 'Пробный прогон ничего не записывает')

    def test_final_import_creates_everything(self):
        """Импорт начальной задолженности: сумма вошла в долг и видна в отчёте должников."""
        from apps.billing.models import Charge
        from apps.reports.services import debtors_report
        from apps.tenants.import_service import run_import
        from apps.tenants.models import Tenant, TenantSpot
        from apps.payments.models import TenantBalance

        file = self._xlsx([
            ['А', 'А-01', 'контейнер', '12.5', 'Иванов Иван', '12345678901234',
             'AN123', '0700', '12000', '', '', '5000'],
            ['А', 'А-02', 'павильон', '20', 'Иванов Иван', '12345678901234',
             'AN123', '0700', '8000', '', '', ''],
            ['Б', 'Б-01', 'бутик', '', 'Петров Пётр', '98765432109876',
             '', '', '15000', '5', '25', ''],
        ])
        report = run_import(file, dry_run=False)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(report.created_buildings, 2)
        self.assertEqual(report.created_spots, 3)
        self.assertEqual(report.created_tenants, 2)
        self.assertEqual(report.created_links, 3)

        ivanov = Tenant.objects.get(inn='12345678901234')
        self.assertEqual(ivanov.tenant_spots.filter(is_active=True).count(), 2)
        initial = Charge.objects.get(tenant=ivanov, source=Charge.Source.INITIAL)
        self.assertEqual(initial.amount, Decimal('5000.00'))
        self.assertEqual(TenantBalance.objects.get(tenant=ivanov).debt_amount,
                         Decimal('5000.00'))
        rows = debtors_report()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['tenant'].pk, ivanov.pk)

        petrov = Tenant.objects.get(inn='98765432109876')
        self.assertEqual(petrov.billing_day, 5)
        self.assertEqual(petrov.payment_day, 25)

    def test_import_rejects_occupied_spot(self):
        from apps.tenants.import_service import run_import
        tenant, tenant_spot = make_tenant_with_spot('9000.00', spot_code='А-01')
        file = self._xlsx([
            ['А', 'А-01', '', '', 'Чужой Человек', '11112222333344',
             '', '', '9000', '', '', ''],
        ])
        report = run_import(file, dry_run=True)
        self.assertFalse(report.ok)
        self.assertIn('уже занято', report.errors[0]['message'])

    def test_import_rejects_bad_days(self):
        from apps.tenants.import_service import run_import
        file = self._xlsx([
            ['А', 'А-05', '', '', 'Иванов', '12312312312312',
             '', '', '5000', '30', '15', ''],
        ])
        report = run_import(file, dry_run=True)
        self.assertFalse(report.ok)
        self.assertTrue(any('раньше срока оплаты' in e['message'] for e in report.errors))
