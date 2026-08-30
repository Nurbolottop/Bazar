from django.conf import settings
from django.db import models

from apps.catalog.models import Building
from apps.core.models import TimestampedModel
from apps.tenants.models import Tenant


class NotificationCode(models.TextChoices):
    """Коды событий уведомлений (ТЗ-00 п. 5.6)."""
    CHARGE_CREATED = 'charge_created', 'Новое начисление (FR-NT-02)'
    REMINDER_10 = 'reminder_10', 'Напоминание за 10 дней'
    REMINDER_5 = 'reminder_5', 'Напоминание за 5 дней'
    REMINDER_3 = 'reminder_3', 'Напоминание за 3 дня'
    REMINDER_0 = 'reminder_0', 'Напоминание в день срока'
    CLAIM_CONFIRMED = 'claim_confirmed', 'Заявка подтверждена, долг погашен'
    CLAIM_CONFIRMED_PARTIAL = 'claim_confirmed_partial', 'Заявка подтверждена, остался долг'
    CLAIM_REJECTED = 'claim_rejected', 'Заявка отклонена (FR-NT-04)'
    OVERDUE = 'overdue', 'Просрочка оплаты (FR-NT-05)'
    ANNOUNCEMENT = 'announcement', 'Объявление администрации'


class NotificationTemplate(TimestampedModel):
    """Шаблон уведомления (FR-NT-07): подстановки {name}, {amount}, {date}, {rest} и т. п."""
    code = models.CharField('Код события', max_length=40, choices=NotificationCode.choices)
    lang = models.CharField('Язык', max_length=2, choices=Tenant.Language.choices)
    title_template = models.CharField('Шаблон заголовка', max_length=255)
    body_template = models.TextField('Шаблон текста')

    class Meta:
        verbose_name = 'Шаблон уведомления'
        verbose_name_plural = 'Шаблоны уведомлений'
        constraints = [
            models.UniqueConstraint(fields=['code', 'lang'], name='uniq_template_code_lang'),
        ]

    def __str__(self):
        return f'{self.code} ({self.lang})'


class Notification(TimestampedModel):
    """Уведомление арендатору (ТЗ-02 п. 3.4).

    Сохраняется всегда, независимо от результата доставки push, —
    арендатор увидит его в ленте приложения (ТЗ-02 п. 9.2).
    """

    class Status(models.TextChoices):
        QUEUED = 'queued', 'В очереди'
        SENT = 'sent', 'Отправлено'
        FAILED = 'failed', 'Не доставлено'

    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.CASCADE, related_name='notifications')
    code = models.CharField('Код события', max_length=40)
    title = models.CharField('Заголовок', max_length=255)
    body = models.TextField('Текст')
    payload = models.JSONField('Полезная нагрузка', default=dict, blank=True)
    channel = models.CharField('Канал', max_length=10, default='push')
    status = models.CharField(
        'Статус', max_length=10, choices=Status.choices, default=Status.QUEUED)
    sent_at = models.DateTimeField('Отправлено', null=True, blank=True)
    read_at = models.DateTimeField('Прочитано', null=True, blank=True)
    error = models.TextField('Ошибка доставки', blank=True)
    attempts = models.PositiveSmallIntegerField('Попыток отправки', default=0)

    class Meta:
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'created_at']),
            models.Index(fields=['status']),
            # FR-NT-10: не более одного напоминания одного типа в сутки
            models.Index(fields=['tenant', 'code', 'created_at']),
        ]

    def __str__(self):
        return f'{self.code} → {self.tenant.full_name}'


class Announcement(TimestampedModel):
    """Объявление администрации (FR-NT-06)."""

    class Audience(models.TextChoices):
        ALL = 'all', 'Всем арендаторам'
        BUILDING = 'building', 'Арендаторам корпуса'
        DEBTORS = 'debtors', 'Только должникам'

    title_ru = models.CharField('Заголовок (рус.)', max_length=255)
    title_ky = models.CharField('Заголовок (кырг.)', max_length=255, blank=True)
    body_ru = models.TextField('Текст (рус.)')
    body_ky = models.TextField('Текст (кырг.)', blank=True)
    audience = models.CharField(
        'Получатели', max_length=10, choices=Audience.choices, default=Audience.ALL)
    building = models.ForeignKey(
        Building, verbose_name='Корпус', null=True, blank=True, on_delete=models.SET_NULL)
    publish_from = models.DateTimeField('Публикация с', null=True, blank=True)
    publish_to = models.DateTimeField('Публикация до', null=True, blank=True)
    sent_at = models.DateTimeField('Разослано', null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Автор',
        null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        verbose_name = 'Объявление'
        verbose_name_plural = 'Объявления'
        ordering = ['-created_at']

    def __str__(self):
        return self.title_ru
