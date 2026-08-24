"""Тесты входа по ИНН, статусов и разграничения доступа (ТЗ-02 п. 7.1, 12.2)."""
import datetime
import tempfile
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import AuthToken, TenantLoginLog
from apps.billing.services import run_billing
from apps.core.models import AuditLog
from apps.core.testutils import make_tenant, make_tenant_with_spot, png_upload
from apps.tenants import services as tenant_services
from apps.tenants.models import Tenant

TMP_MEDIA = tempfile.mkdtemp(prefix='bazar_test_media_')


class LoginTests(TestCase):
    def test_login_success_and_log(self):
        tenant = make_tenant(inn='11111111111111')
        result, raw_key = services.tenant_login(inn='11111111111111', ip='1.2.3.4')
        self.assertEqual(result.pk, tenant.pk)
        token = AuthToken.objects.get(tenant=tenant)
        self.assertEqual(token.key, AuthToken.hash_key(raw_key))
        self.assertNotEqual(token.key, raw_key, 'В базе хранится только хеш')
        log = TenantLoginLog.objects.get()
        self.assertTrue(log.success)
        self.assertEqual(log.ip, '1.2.3.4')

    def test_unknown_inn_fails_and_logged(self):
        with self.assertRaises(services.LoginFailed):
            services.tenant_login(inn='99999999999999', ip='1.2.3.4')
        log = TenantLoginLog.objects.get()
        self.assertFalse(log.success)

    def test_rate_limit_10_per_hour(self):
        """Не более 10 попыток входа с одного IP в час (ТЗ-00 п. 8.1)."""
        for _ in range(10):
            try:
                services.tenant_login(inn='0000', ip='5.5.5.5')
            except services.LoginFailed:
                pass
        with self.assertRaises(services.LoginRateLimited):
            services.tenant_login(inn='0000', ip='5.5.5.5')

    def test_suspended_can_login(self):
        make_tenant(inn='22222222222222', status=Tenant.Status.SUSPENDED)
        tenant, _ = services.tenant_login(inn='22222222222222')
        self.assertEqual(tenant.status, Tenant.Status.SUSPENDED)

    def test_archived_cannot_login_and_tokens_revoked(self):
        """Архивный арендатор: вход невозможен, ранее выданный токен отозван."""
        tenant = make_tenant(inn='33333333333333')
        _, raw_key = services.tenant_login(inn='33333333333333')
        tenant_services.set_tenant_status(tenant=tenant, status=Tenant.Status.ARCHIVED)
        with self.assertRaises(services.LoginFailed):
            services.tenant_login(inn='33333333333333')
        token = AuthToken.objects.get(tenant=tenant)
        self.assertIsNotNone(token.revoked_at)
        # отозванный токен не проходит аутентификацию API
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {raw_key}')
        response = client.get('/api/v1/me')
        self.assertEqual(response.status_code, 401)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ApiAccessTests(TestCase):
    def setUp(self):
        self.tenant, _ = make_tenant_with_spot('12000.00', inn='44444444444444')
        self.other, _ = make_tenant_with_spot('9000.00', inn='55555555555555')
        run_billing(today=datetime.date(2026, 3, 1))
        _, self.key = services.tenant_login(inn='44444444444444')
        self.client_api = APIClient()
        self.client_api.credentials(HTTP_AUTHORIZATION=f'Token {self.key}')

    def test_summary(self):
        response = self.client_api.get('/api/v1/me/summary')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['debt'], '12000.00')
        self.assertEqual(response.data['payment_status'], 'awaiting')

    def test_own_charge_accessible(self):
        charge_id = self.tenant.charges.first().pk
        response = self.client_api.get(f'/api/v1/me/charges/{charge_id}')
        self.assertEqual(response.status_code, 200)

    def test_foreign_charge_404_and_logged(self):
        """Попытка получить начисление чужого арендатора: 404, попытка в журнале."""
        foreign_id = self.other.charges.first().pk
        response = self.client_api.get(f'/api/v1/me/charges/{foreign_id}')
        self.assertEqual(response.status_code, 404)
        entry = AuditLog.objects.filter(
            action='access_denied', model_name='Charge', object_id=str(foreign_id))
        self.assertTrue(entry.exists())

    def test_suspended_tenant_cannot_submit_claim(self):
        """Приостановленный арендатор: просмотр доступен, подача заявки — 403."""
        self.tenant.status = Tenant.Status.SUSPENDED
        self.tenant.save()
        response = self.client_api.get('/api/v1/me/summary')
        self.assertEqual(response.status_code, 200)
        response = self.client_api.post('/api/v1/payment-claims', {
            'declared_amount': '1000.00',
            'receipt_image': png_upload(),
            'idempotency_key': 'suspended-1',
        }, format='multipart')
        self.assertEqual(response.status_code, 403)

    def test_claim_via_api(self):
        response = self.client_api.post('/api/v1/payment-claims', {
            'declared_amount': '5000.00',
            'receipt_image': png_upload(),
            'idempotency_key': 'api-claim-1',
        }, format='multipart')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'pending')
        # долг не изменился
        summary = self.client_api.get('/api/v1/me/summary')
        self.assertEqual(summary.data['debt'], '12000.00')
        self.assertTrue(summary.data['has_pending_claim'])

    def test_withdraw_via_api(self):
        create = self.client_api.post('/api/v1/payment-claims', {
            'declared_amount': '5000.00',
            'receipt_image': png_upload(),
            'idempotency_key': 'api-claim-2',
        }, format='multipart')
        claim_id = create.data['id']
        response = self.client_api.post(f'/api/v1/payment-claims/{claim_id}/withdraw')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'withdrawn')
        # повторный отзыв — конфликт
        response = self.client_api.post(f'/api/v1/payment-claims/{claim_id}/withdraw')
        self.assertEqual(response.status_code, 409)

    def test_history_contains_adjustments(self):
        from apps.payments.services import adjust_debt
        adjust_debt(tenant=self.tenant, amount=Decimal('-1000.00'),
                    reason='Скидка', actor=None)
        response = self.client_api.get('/api/v1/me/payments')
        self.assertEqual(response.status_code, 200)
        types = [item['type'] for item in response.data['results']]
        self.assertIn('adjustment', types)

    def test_unauthenticated_401(self):
        response = APIClient().get('/api/v1/me/summary')
        self.assertEqual(response.status_code, 401)
