"""API профиля арендатора: /me, /me/summary, /me/spots, /me/settings (ТЗ-02 п. 6.2)."""
import datetime

from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Charge
from apps.core.money import ZERO
from apps.core.services import audit
from apps.payments.models import PaymentClaim

from .models import Tenant, TenantSpot


class ProfileView(APIView):
    """GET /api/v1/me — профиль арендатора."""

    def get(self, request):
        tenant: Tenant = request.user
        return Response({
            'full_name': tenant.full_name,
            'inn': tenant.inn,
            'phone': tenant.phone,
            'status': tenant.status,
            'language': tenant.language,
        })


class SummaryView(APIView):
    """GET /api/v1/me/summary — сводка для главного экрана (все суммы считает сервер)."""

    def get(self, request):
        from apps.payments.services import recalc_balance

        tenant: Tenant = request.user
        balance = getattr(tenant, 'balance', None)
        if balance is None:
            balance = recalc_balance(tenant)

        today = timezone.localdate()
        open_charges = tenant.charges.exclude(
            status__in=[Charge.Status.CANCELLED, Charge.Status.PAID])
        overdue_amount = sum(
            (c.remaining for c in open_charges.filter(status=Charge.Status.OVERDUE)), ZERO)

        next_due = open_charges.filter(due_date__gte=today) \
                               .order_by('due_date').values_list('due_date', flat=True).first()
        days_until_due = (next_due - today).days if next_due else None

        if balance.debt_amount <= ZERO:
            payment_status = 'no_debt'
        elif overdue_amount > ZERO:
            payment_status = 'overdue'
        elif days_until_due is not None and days_until_due <= 3:
            payment_status = 'due_soon'
        else:
            payment_status = 'awaiting'

        has_pending_claim = tenant.claims.filter(
            status=PaymentClaim.Status.PENDING).exists()

        # «Оплачено X из Y» по текущему периоду (ТЗ-01 §5.1)
        period_charges = tenant.charges.filter(
            period_year=today.year, period_month=today.month,
        ).exclude(status=Charge.Status.CANCELLED)
        total_charged = sum((c.amount for c in period_charges), ZERO)
        paid_amount = sum((c.paid_amount for c in period_charges), ZERO)

        return Response({
            'debt': str(balance.debt_amount),
            'paid_amount': str(paid_amount),
            'total_charged': str(total_charged),
            'overdue_amount': str(overdue_amount),
            'advance': str(balance.advance_amount),
            'amount_due': str(balance.debt_amount),
            'next_due_date': next_due,
            'days_until_due': days_until_due,
            'payment_status': payment_status,
            'has_pending_claim': has_pending_claim,
            'tenant_status': tenant.status,
        })


class SpotSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source='spot.code')
    building = serializers.SerializerMethodField()
    spot_type = serializers.CharField(source='spot.spot_type')
    area_sqm = serializers.DecimalField(
        source='spot.area_sqm', max_digits=8, decimal_places=2)
    photo = serializers.ImageField(source='spot.photo')
    payment_status = serializers.SerializerMethodField()
    debt = serializers.SerializerMethodField()
    next_due_date = serializers.SerializerMethodField()

    class Meta:
        model = TenantSpot
        fields = ['id', 'code', 'building', 'spot_type', 'area_sqm',
                  'monthly_amount', 'start_date', 'photo',
                  'payment_status', 'debt', 'next_due_date']

    def get_building(self, obj) -> str:
        return obj.spot.building.name if obj.spot.building_id else ''

    def _pay_state(self, obj) -> dict:
        """Состояние оплаты по месту (ТЗ-01 §5.2) — словарь тот же, что в /me/summary."""
        state = getattr(obj, '_pay_state_cache', None)
        if state is not None:
            return state
        today = timezone.localdate()
        open_charges = [
            c for c in obj.charges.all()
            if c.status not in (Charge.Status.CANCELLED, Charge.Status.PAID)]
        debt = sum((c.remaining for c in open_charges), ZERO)
        has_overdue = any(
            c.status == Charge.Status.OVERDUE and c.remaining > ZERO for c in open_charges)
        future_due = sorted(c.due_date for c in open_charges if c.due_date >= today)
        next_due = future_due[0] if future_due else None
        if debt <= ZERO:
            payment_status = 'no_debt'
        elif has_overdue:
            payment_status = 'overdue'
        elif next_due is not None and (next_due - today).days <= 3:
            payment_status = 'due_soon'
        else:
            payment_status = 'awaiting'
        state = {'payment_status': payment_status, 'debt': str(debt),
                 'next_due_date': next_due}
        obj._pay_state_cache = state
        return state

    def get_payment_status(self, obj) -> str:
        return self._pay_state(obj)['payment_status']

    def get_debt(self, obj) -> str:
        return self._pay_state(obj)['debt']

    def get_next_due_date(self, obj):
        return self._pay_state(obj)['next_due_date']


class MySpotsView(APIView):
    """GET /api/v1/me/spots — торговые места арендатора с суммами."""

    def get(self, request):
        queryset = TenantSpot.objects.filter(
            tenant=request.user, is_active=True,
        ).select_related('spot', 'spot__building').prefetch_related('charges')
        return Response(SpotSerializer(queryset, many=True, context={'request': request}).data)


class MySpotDetailView(APIView):
    """GET /api/v1/me/spots/{id} — карточка места."""

    def get(self, request, pk: int):
        # Принадлежность проверяется на уровне queryset (ТЗ-02 п. 7.3);
        # чужой объект неотличим от несуществующего — 404
        try:
            tenant_spot = TenantSpot.objects.select_related('spot', 'spot__building').get(
                pk=pk, tenant=request.user)
        except TenantSpot.DoesNotExist:
            if TenantSpot.objects.filter(pk=pk).exists():
                audit(action='access_denied', model_name='TenantSpot', object_id=pk,
                      actor_type='tenant', new_value={'tenant_inn': request.user.inn})
            raise Http404
        return Response(SpotSerializer(tenant_spot, context={'request': request}).data)


class SettingsView(APIView):
    """PATCH /api/v1/me/settings — язык интерфейса и настройки уведомлений."""

    def patch(self, request):
        tenant: Tenant = request.user
        updated = []
        language = request.data.get('language')
        if language is not None:
            if language not in dict(Tenant.Language.choices):
                return Response(
                    {'code': 'validation_error', 'message': 'Недопустимый язык.',
                     'details': {'language': ['ru или ky']}}, status=400)
            tenant.language = language
            updated.append('language')
        announcements = request.data.get('announcements_enabled')
        if announcements is not None:
            tenant.announcements_enabled = bool(announcements)
            updated.append('announcements_enabled')
        if updated:
            tenant.save(update_fields=updated + ['updated_at'])
        return Response({
            'language': tenant.language,
            'announcements_enabled': tenant.announcements_enabled,
        })
