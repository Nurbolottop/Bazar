from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class TimestampedModel(models.Model):
    """Абстрактная база: created_at и updated_at обязательны во всех моделях (ТЗ-02 раздел 3)."""
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        abstract = True


def default_reminder_days():
    return [10, 5, 3, 0]


def default_overdue_notice_days():
    return [1]


def default_reject_reasons():
    return [
        'Чек нечитаемый',
        'Сумма не совпадает',
        'Платёж не найден в выписке',
    ]


DAY_VALIDATORS = [MinValueValidator(1), MaxValueValidator(31)]


class SystemSettings(TimestampedModel):
    """Настройки Системы (ТЗ-02 п. 3.4). Одна запись, pk=1."""
    market_name = models.CharField('Наименование рынка', max_length=255, blank=True)
    contacts = models.TextField('Контакты рынка', blank=True)
    qr_image = models.ImageField('QR-код для оплаты', upload_to='qr/', blank=True, null=True)
    payment_instruction_ru = models.TextField('Инструкция по оплате (рус.)', blank=True)
    payment_instruction_ky = models.TextField('Инструкция по оплате (кырг.)', blank=True)
    default_billing_day = models.PositiveSmallIntegerField(
        'Дата начисления по умолчанию', default=1, validators=DAY_VALIDATORS)
    default_payment_day = models.PositiveSmallIntegerField(
        'Срок оплаты по умолчанию', default=30, validators=DAY_VALIDATORS)
    reminder_days = models.JSONField(
        'Дни напоминаний до срока оплаты', default=default_reminder_days)
    overdue_notice_days = models.JSONField(
        'Дни уведомления о просрочке (после срока)', default=default_overdue_notice_days)
    reject_reasons = models.JSONField(
        'Причины отклонения заявок', default=default_reject_reasons)
    pin_login_enabled = models.BooleanField('Вход по PIN-коду включён', default=False)
    consent_version = models.CharField(
        'Версия текста согласия на обработку ПДн', max_length=20, default='1.0')
    min_app_version = models.CharField(
        'Минимальная версия приложения', max_length=20, blank=True,
        help_text='Отдаётся в GET /app/config')

    class Meta:
        verbose_name = 'Настройки системы'
        verbose_name_plural = 'Настройки системы'

    def __str__(self):
        return 'Настройки системы'

    def clean(self):
        # FR-CH-09: дата начисления всегда строго раньше срока оплаты
        if self.default_billing_day >= self.default_payment_day:
            raise ValidationError(
                'Дата начисления должна быть строго раньше срока оплаты. '
                'Иначе напоминания о сроке оплаты отправить невозможно.'
            )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'SystemSettings':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AuditLog(models.Model):
    """Журнал действий (ТЗ-02 п. 3.4). Только добавление; изменение и удаление запрещены."""

    class ActorType(models.TextChoices):
        ADMIN = 'admin', 'Администратор'
        TENANT = 'tenant', 'Арендатор'
        SYSTEM = 'system', 'Система'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Администратор',
        null=True, blank=True, on_delete=models.PROTECT, related_name='audit_entries')
    actor_type = models.CharField(
        'Тип субъекта', max_length=10, choices=ActorType.choices, default=ActorType.ADMIN)
    action = models.CharField('Действие', max_length=100)
    model_name = models.CharField('Модель', max_length=100)
    object_id = models.CharField('ID объекта', max_length=64)
    old_value = models.JSONField('Прежнее значение', null=True, blank=True)
    new_value = models.JSONField('Новое значение', null=True, blank=True)
    ip = models.GenericIPAddressField('IP-адрес', null=True, blank=True)
    user_agent = models.CharField('User-Agent', max_length=512, blank=True)
    created_at = models.DateTimeField('Время', auto_now_add=True)

    class Meta:
        verbose_name = 'Запись журнала'
        verbose_name_plural = 'Журнал действий'
        indexes = [
            models.Index(fields=['model_name', 'object_id', 'created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.action} {self.model_name}#{self.object_id}'

    def delete(self, *args, **kwargs):
        raise PermissionError('Записи журнала не удаляются (FR-AD-04)')
