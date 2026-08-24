"""API заявок, платежей и истории операций (ТЗ-02 п. 6.2)."""
import io

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse, Http404
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.exceptions import Conflict
from apps.core.api.pagination import DefaultPagination
from apps.core.services import audit
from apps.tenants.models import Tenant

from . import services
from .models import DebtAdjustment, Payment, PaymentClaim


def validate_receipt(uploaded_file):
    """JPG или PNG до 10 МБ, проверка сигнатуры файла, а не расширения (ТЗ-02 п. 4.3, 9.1)."""
    from PIL import Image

    if uploaded_file.size > settings.RECEIPT_MAX_SIZE:
        raise ValidationError(
            {'receipt_image': ['Файл чека превышает допустимый размер 10 МБ.']},
            code='file_too_large')
    try:
        position = uploaded_file.tell()
        image = Image.open(uploaded_file)
        image.verify()
        uploaded_file.seek(position)
        detected = image.format
    except Exception:
        raise ValidationError(
            {'receipt_image': ['Файл не является изображением JPG или PNG.']})
    if detected not in settings.RECEIPT_ALLOWED_FORMATS:
        raise ValidationError(
            {'receipt_image': [f'Допустимы только JPG и PNG, получен {detected}.']})


class ClaimCreateSerializer(serializers.Serializer):
    declared_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    receipt_image = serializers.ImageField()
    comment = serializers.CharField(required=False, allow_blank=True, default='')
    idempotency_key = serializers.CharField(max_length=64)


class ClaimSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentClaim
        fields = ['id', 'declared_amount', 'status', 'submitted_at',
                  'reviewed_at', 'reject_reason', 'comment']


class ClaimCreateView(APIView):
    """POST /api/v1/payment-claims — подача заявки об оплате (multipart)."""
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        tenant: Tenant = request.user
        serializer = ClaimCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        validate_receipt(data['receipt_image'])

        try:
            claim = services.create_claim(
                tenant=tenant,
                declared_amount=data['declared_amount'],
                receipt_image=data['receipt_image'],
                idempotency_key=data['idempotency_key'],
                comment=data['comment'],
                device_info=request.META.get('HTTP_USER_AGENT', ''))
        except PermissionError as exc:
            raise PermissionDenied(str(exc))
        except DjangoValidationError as exc:
            raise ValidationError({'declared_amount': exc.messages})

        return Response(ClaimSerializer(claim).data, status=status.HTTP_201_CREATED)


class ClaimWithdrawView(APIView):
    """POST /api/v1/payment-claims/{id}/withdraw — отзыв собственной заявки (FR-PM-14)."""

    def post(self, request, pk: int):
        claim = PaymentClaim.objects.filter(pk=pk, tenant=request.user).first()
        if claim is None:
            if PaymentClaim.objects.filter(pk=pk).exists():
                audit(action='access_denied', model_name='PaymentClaim', object_id=pk,
                      actor_type='tenant', new_value={'tenant_inn': request.user.inn})
            raise Http404
        try:
            claim = services.withdraw_claim(claim=claim, tenant=request.user)
        except services.ClaimAlreadyProcessed as exc:
            raise Conflict(str(exc))
        return Response(ClaimSerializer(claim).data)


class HistoryView(APIView):
    """GET /api/v1/me/payments — история заявок, платежей и корректировок.

    Каждая запись содержит тип: claim, payment или adjustment (ТЗ-02 п. 6.2).
    """

    def get(self, request):
        tenant: Tenant = request.user
        items = []
        for claim in tenant.claims.all():
            items.append({
                'id': f'claim-{claim.pk}',
                'type': 'claim',
                'date': claim.submitted_at,
                'amount': str(claim.declared_amount),
                'status': claim.status,
                'reject_reason': claim.reject_reason,
                'comment': claim.comment,
            })
        for payment in tenant.payments.filter(source=Payment.Source.MANUAL):
            items.append({
                'id': f'payment-{payment.pk}',
                'type': 'payment',
                'date': payment.paid_at,
                'amount': str(payment.amount),
                'status': payment.status,
                'comment': payment.comment or 'Внесён администрацией',
            })
        # Корректировки долга отображаются арендатору отдельной строкой (FR-PM-15)
        for adjustment in tenant.adjustments.filter(status=DebtAdjustment.Status.ACTIVE):
            items.append({
                'id': f'adjustment-{adjustment.pk}',
                'type': 'adjustment',
                'date': adjustment.created_at,
                'amount': str(adjustment.amount),
                'status': adjustment.status,
                'reason': adjustment.reason,
            })

        items.sort(key=lambda item: item['date'], reverse=True)
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(items, request)
        return paginator.get_paginated_response(page)


class HistoryDetailView(APIView):
    """GET /api/v1/me/payments/{id} — детали платежа или заявки, включая ссылку на чек."""

    def get(self, request, item_id: str):
        tenant: Tenant = request.user
        kind, _, pk = item_id.partition('-')
        if not pk.isdigit():
            raise Http404
        pk = int(pk)

        if kind == 'claim':
            claim = self._get_or_404(PaymentClaim, pk, tenant)
            data = ClaimSerializer(claim).data
            data['type'] = 'claim'
            data['receipt_url'] = f'/api/v1/me/receipts/{claim.pk}'
            payment = claim.payments.filter(status=Payment.Status.ACTIVE).first()
            data['accepted_amount'] = str(payment.amount) if payment else None
            return Response(data)
        if kind == 'payment':
            payment = self._get_or_404(Payment, pk, tenant)
            data = {
                'id': f'payment-{payment.pk}', 'type': 'payment',
                'amount': str(payment.amount), 'status': payment.status,
                'paid_at': payment.paid_at, 'source': payment.source,
                'comment': payment.comment,
            }
            if payment.claim_id:
                data['receipt_url'] = f'/api/v1/me/receipts/{payment.claim_id}'
            return Response(data)
        if kind == 'adjustment':
            adjustment = self._get_or_404(DebtAdjustment, pk, tenant)
            return Response({
                'id': f'adjustment-{adjustment.pk}', 'type': 'adjustment',
                'amount': str(adjustment.amount), 'status': adjustment.status,
                'reason': adjustment.reason, 'date': adjustment.created_at,
            })
        raise Http404

    @staticmethod
    def _get_or_404(model, pk: int, tenant: Tenant):
        obj = model.objects.filter(pk=pk, tenant=tenant).first()
        if obj is None:
            if model.objects.filter(pk=pk).exists():
                audit(action='access_denied', model_name=model.__name__, object_id=pk,
                      actor_type='tenant', new_value={'tenant_inn': tenant.inn})
            raise Http404
        return obj


class ReceiptView(APIView):
    """GET /api/v1/me/receipts/{claim_id} — фотография чека с проверкой прав (ТЗ-02 п. 7.3).

    Файлы не отдаются веб-сервером напрямую без аутентификации.
    """

    def get(self, request, claim_id: int):
        claim = PaymentClaim.objects.filter(pk=claim_id, tenant=request.user).first()
        if claim is None or not claim.receipt_image:
            raise Http404
        return FileResponse(claim.receipt_image.open('rb'))
