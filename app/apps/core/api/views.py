"""Общие методы API: /payment-info и /app/config (ТЗ-02 п. 6.2)."""
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import SystemSettings


class PaymentInfoView(APIView):
    """GET /api/v1/payment-info — QR-код рынка и инструкция на языке арендатора (FR-QR-01..03)."""

    def get(self, request):
        s = SystemSettings.load()
        lang = getattr(request.user, 'language', None) or 'ru'
        accept = request.headers.get('Accept-Language', '')
        if accept.lower().startswith('ky'):
            lang = 'ky'
        instruction = s.payment_instruction_ky if lang == 'ky' and s.payment_instruction_ky \
            else s.payment_instruction_ru
        qr_url = None
        if s.qr_image:
            qr_url = request.build_absolute_uri(s.qr_image.url)
        return Response({
            'qr_image': qr_url,
            'instruction': instruction,
            'market_name': s.market_name,
        })


class AppConfigView(APIView):
    """GET /api/v1/app/config — конфигурация приложения. Доступна без токена."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        s = SystemSettings.load()
        return Response({
            'min_app_version': s.min_app_version,
            'contacts': s.contacts,
            'market_name': s.market_name,
            'reminder_days': s.reminder_days,
            'pin_login_enabled': s.pin_login_enabled,
            'consent_version': s.consent_version,
        })
