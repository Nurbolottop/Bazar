"""Аутентификация мобильного приложения: Authorization: Token <ключ> (ТЗ-02 п. 6.1)."""
from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.tenants.models import Tenant

from .models import AuthToken


class TenantTokenAuthentication(authentication.BaseAuthentication):
    keyword = 'Token'

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode('utf-8', 'ignore')
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            return None

        token = AuthToken.objects.select_related('tenant').filter(
            key=AuthToken.hash_key(parts[1]), revoked_at__isnull=True).first()
        if token is None:
            raise exceptions.AuthenticationFailed('Токен отсутствует или недействителен.')

        tenant = token.tenant
        if tenant.status == Tenant.Status.ARCHIVED:
            token.revoke()
            raise exceptions.AuthenticationFailed('Доступ запрещён.')

        token.last_used_at = timezone.now()
        token.save(update_fields=['last_used_at'])
        return tenant, token

    def authenticate_header(self, request):
        return self.keyword
