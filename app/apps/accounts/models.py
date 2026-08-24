import hashlib
import secrets

from django.db import models
from django.utils import timezone

from apps.core.models import TimestampedModel
from apps.tenants.models import Tenant


class Device(TimestampedModel):
    """Устройство арендатора для push-уведомлений (ТЗ-02 п. 3.4).

    Поле locale — справочное: язык уведомлений определяется только Tenant.language.
    """

    class Platform(models.TextChoices):
        ANDROID = 'android', 'Android'
        IOS = 'ios', 'iOS'

    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.CASCADE, related_name='devices')
    push_token = models.CharField('Push-токен', max_length=512, blank=True)
    platform = models.CharField('Платформа', max_length=10, choices=Platform.choices)
    app_version = models.CharField('Версия приложения', max_length=20, blank=True)
    locale = models.CharField('Локаль устройства', max_length=10, blank=True)
    last_seen_at = models.DateTimeField('Последняя активность', null=True, blank=True)
    is_active = models.BooleanField('Действует', default=True)

    class Meta:
        verbose_name = 'Устройство'
        verbose_name_plural = 'Устройства'

    def __str__(self):
        return f'{self.get_platform_display()} ({self.tenant.full_name})'


class AuthToken(models.Model):
    """Токен доступа арендатора (ТЗ-02 п. 3.4, 7.1).

    В базе хранится только SHA-256-хеш ключа. Срока истечения нет: токен
    отзывается при архивации арендатора, действием администратора или выходом.
    """
    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.CASCADE, related_name='auth_tokens')
    key = models.CharField('Ключ (SHA-256)', max_length=64, unique=True)
    created_at = models.DateTimeField('Создан', auto_now_add=True)
    last_used_at = models.DateTimeField('Использован', null=True, blank=True)
    revoked_at = models.DateTimeField('Отозван', null=True, blank=True)
    device_info = models.CharField('Устройство', max_length=255, blank=True)
    ip = models.GenericIPAddressField('IP-адрес', null=True, blank=True)

    class Meta:
        verbose_name = 'Токен арендатора'
        verbose_name_plural = 'Токены арендаторов'

    def __str__(self):
        state = 'отозван' if self.revoked_at else 'действует'
        return f'Токен {self.tenant.full_name} ({state})'

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def issue(cls, tenant: Tenant, device_info: str = '', ip: str | None = None) -> tuple['AuthToken', str]:
        """Выпустить токен. Возвращает (объект, открытый ключ) — ключ показывается один раз."""
        raw_key = secrets.token_hex(20)
        token = cls.objects.create(
            tenant=tenant, key=cls.hash_key(raw_key),
            device_info=device_info[:255], ip=ip)
        return token, raw_key

    def revoke(self):
        if not self.revoked_at:
            self.revoked_at = timezone.now()
            self.save(update_fields=['revoked_at'])


class AdminLoginLog(models.Model):
    """Попытки входа администраторов: блокировка после 5 неудач на 15 минут (ТЗ-02 п. 7.2)."""
    username = models.CharField('Логин', max_length=150)
    success = models.BooleanField('Успешно')
    ip = models.GenericIPAddressField('IP-адрес', null=True, blank=True)
    created_at = models.DateTimeField('Время', auto_now_add=True)

    class Meta:
        verbose_name = 'Вход администратора'
        verbose_name_plural = 'Журнал входов администраторов'
        indexes = [models.Index(fields=['username', 'created_at'])]


class TenantLoginLog(models.Model):
    """Журнал входов арендаторов (ТЗ-00 п. 8.1): все попытки, успешные и нет."""
    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='login_logs')
    inn_attempted = models.CharField('Введённый ИНН', max_length=20)
    success = models.BooleanField('Успешно')
    ip = models.GenericIPAddressField('IP-адрес', null=True, blank=True)
    user_agent = models.CharField('User-Agent / устройство', max_length=512, blank=True)
    created_at = models.DateTimeField('Время', auto_now_add=True)

    class Meta:
        verbose_name = 'Вход арендатора'
        verbose_name_plural = 'Журнал входов арендаторов'
        indexes = [
            models.Index(fields=['ip', 'created_at']),
            models.Index(fields=['inn_attempted', 'created_at']),
        ]

    def __str__(self):
        result = 'вход' if self.success else 'отказ'
        return f'{result} {self.inn_attempted} {self.created_at:%d.%m.%Y %H:%M}'
