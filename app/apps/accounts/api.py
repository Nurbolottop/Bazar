"""API аутентификации и устройств (ТЗ-02 п. 6.2)."""
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services import audit

from . import services
from .models import Device


class LoginSerializer(serializers.Serializer):
    inn = serializers.CharField(max_length=20)
    device_info = serializers.CharField(max_length=512, required=False, default='')
    consent_accepted = serializers.BooleanField(required=False, default=False)
    pin = serializers.CharField(max_length=10, required=False, allow_null=True, default=None)


def client_ip(request) -> str | None:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


class LoginView(APIView):
    """POST /api/v1/auth/login — вход по ИНН (ТЗ-00 п. 8.1)."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []  # собственное ограничение по журналу входов

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            tenant, raw_key = services.tenant_login(
                inn=data['inn'], device_info=data['device_info'],
                ip=client_ip(request), consent_accepted=data['consent_accepted'],
                pin=data['pin'])
        except services.LoginRateLimited as exc:
            return Response(
                {'code': 'throttled', 'message': exc.message, 'details': {}},
                status=status.HTTP_429_TOO_MANY_REQUESTS)
        except services.PinRequired as exc:
            return Response(
                {'code': 'pin_required', 'message': exc.message, 'details': {}},
                status=status.HTTP_400_BAD_REQUEST)
        except services.LoginFailed as exc:
            return Response(
                {'code': 'login_failed', 'message': exc.message, 'details': {}},
                status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'token': raw_key,
            'profile': {
                'full_name': tenant.full_name,
                'inn': tenant.inn,
                'phone': tenant.phone,
                'status': tenant.status,
                'language': tenant.language,
                'consent_accepted_at': tenant.consent_accepted_at,
            },
        }, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """POST /api/v1/auth/logout — отзыв токена и деактивация устройства."""

    def post(self, request):
        token = request.auth
        if token is not None:
            token.revoke()
            Device.objects.filter(
                tenant=request.user, push_token__isnull=False,
            ).filter(push_token=request.data.get('push_token', '')).update(is_active=False)
        return Response({'detail': 'ok'})


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ['push_token', 'platform', 'app_version', 'locale']


class DeviceRegisterView(APIView):
    """POST /api/v1/me/devices — регистрация push-токена устройства."""

    def post(self, request):
        serializer = DeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        device, _ = Device.objects.update_or_create(
            tenant=request.user, push_token=data['push_token'],
            defaults={
                'platform': data['platform'],
                'app_version': data.get('app_version', ''),
                'locale': data.get('locale', ''),
                'last_seen_at': timezone.now(),
                'is_active': True,
            })
        return Response({'id': device.pk}, status=status.HTTP_201_CREATED)
