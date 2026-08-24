"""API начислений арендатора: /me/charges (ТЗ-02 п. 6.2)."""
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.pagination import DefaultPagination
from apps.core.services import audit
from apps.payments.models import PaymentAllocation

from .models import Charge


class ChargeSerializer(serializers.ModelSerializer):
    spot_code = serializers.CharField(source='tenant_spot.spot.code', default=None)
    remaining = serializers.SerializerMethodField()

    class Meta:
        model = Charge
        fields = ['id', 'period_year', 'period_month', 'amount', 'paid_amount',
                  'remaining', 'charged_date', 'due_date', 'status', 'source',
                  'comment', 'spot_code']

    def get_remaining(self, obj) -> str:
        return str(obj.remaining)


class AllocationSerializer(serializers.ModelSerializer):
    payment_id = serializers.IntegerField(allow_null=True)
    paid_at = serializers.DateTimeField(source='payment.paid_at', default=None)

    class Meta:
        model = PaymentAllocation
        fields = ['id', 'amount', 'kind', 'payment_id', 'paid_at', 'created_at']


class MyChargesView(APIView):
    """GET /api/v1/me/charges — начисления с фильтрами: месяц, место, статус, страница."""

    def get(self, request):
        queryset = Charge.objects.filter(tenant=request.user) \
            .select_related('tenant_spot__spot').order_by('-due_date', '-id')

        period = request.query_params.get('period')  # формат YYYY-MM
        if period:
            try:
                year, month = period.split('-')
                queryset = queryset.filter(period_year=int(year), period_month=int(month))
            except (ValueError, TypeError):
                pass
        spot_id = request.query_params.get('spot')
        if spot_id and spot_id.isdigit():
            queryset = queryset.filter(tenant_spot__spot_id=int(spot_id))
        status_filter = request.query_params.get('status')
        if status_filter in dict(Charge.Status.choices):
            queryset = queryset.filter(status=status_filter)

        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        return paginator.get_paginated_response(ChargeSerializer(page, many=True).data)


class MyChargeDetailView(APIView):
    """GET /api/v1/me/charges/{id} — детали начисления с перечнем зачтённых платежей."""

    def get(self, request, pk: int):
        try:
            charge = Charge.objects.select_related('tenant_spot__spot').get(
                pk=pk, tenant=request.user)
        except Charge.DoesNotExist:
            # Чужой объект неотличим от несуществующего (ТЗ-02 п. 6.3),
            # попытка доступа к чужому фиксируется в журнале (п. 7.3)
            if Charge.objects.filter(pk=pk).exists():
                audit(action='access_denied', model_name='Charge', object_id=pk,
                      actor_type='tenant', new_value={'tenant_inn': request.user.inn})
            raise Http404

        data = ChargeSerializer(charge).data
        allocations = charge.allocations.filter(
            status=PaymentAllocation.Status.ACTIVE).select_related('payment')
        data['allocations'] = AllocationSerializer(allocations, many=True).data
        return Response(data)
