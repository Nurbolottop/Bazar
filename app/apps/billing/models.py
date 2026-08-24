from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel
from apps.core.money import MoneyField, ZERO
from apps.tenants.models import Tenant, TenantSpot


class Charge(TimestampedModel):
    """Начисление (ТЗ-02 п. 3.3).

    Финансовые записи не удаляются: отмена — перевод в статус cancelled (FR-CH-05).
    """

    class Status(models.TextChoices):
        UNPAID = 'unpaid', 'Не оплачено'
        PARTIAL = 'partial', 'Оплачено частично'
        PAID = 'paid', 'Оплачено'
        OVERDUE = 'overdue', 'Просрочено'
        CANCELLED = 'cancelled', 'Отменено'

    class Source(models.TextChoices):
        AUTO = 'auto', 'Автоматическое'
        MANUAL = 'manual', 'Ручное'
        INITIAL = 'initial', 'Долг на начало работы Системы'
        ADJUSTMENT = 'adjustment', 'Корректировка долга (в плюс)'

    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.PROTECT, related_name='charges')
    tenant_spot = models.ForeignKey(
        TenantSpot, verbose_name='Место арендатора', on_delete=models.PROTECT,
        related_name='charges', null=True, blank=True,
        help_text='Пусто для разовых начислений и начальной задолженности')
    period_year = models.PositiveSmallIntegerField('Год периода')
    period_month = models.PositiveSmallIntegerField('Месяц периода')
    amount = MoneyField('Сумма')
    paid_amount = MoneyField('Оплачено', default=ZERO)
    charged_date = models.DateField('Дата начисления')
    due_date = models.DateField('Срок оплаты')
    status = models.CharField(
        'Статус', max_length=10, choices=Status.choices, default=Status.UNPAID)
    source = models.CharField(
        'Происхождение', max_length=12, choices=Source.choices, default=Source.AUTO)
    comment = models.TextField('Комментарий', blank=True)
    cancelled_reason = models.TextField('Причина отмены', blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто отменил',
        null=True, blank=True, on_delete=models.PROTECT, related_name='cancelled_charges')

    class Meta:
        verbose_name = 'Начисление'
        verbose_name_plural = 'Начисления'
        ordering = ['due_date', 'id']
        indexes = [
            models.Index(fields=['tenant', 'status', 'due_date']),
            models.Index(fields=['period_year', 'period_month']),
        ]
        constraints = [
            # FR-CH-08: повторный прогон за тот же месяц не создаёт дублей
            models.UniqueConstraint(
                fields=['tenant_spot', 'period_year', 'period_month'],
                condition=models.Q(source='auto') & ~models.Q(status='cancelled'),
                name='uniq_auto_charge_per_spot_period'),
            # paid_amount в пределах [0, amount]
            models.CheckConstraint(
                check=models.Q(paid_amount__gte=0) & models.Q(paid_amount__lte=models.F('amount')),
                name='charge_paid_within_amount'),
            models.CheckConstraint(check=models.Q(amount__gte=0), name='charge_amount_gte_0'),
            # Дата начисления строго раньше срока оплаты (FR-CH-09)
            models.CheckConstraint(
                check=models.Q(charged_date__lt=models.F('due_date')),
                name='charge_charged_before_due'),
        ]

    def __str__(self):
        return f'Начисление #{self.pk} {self.tenant.full_name} {self.amount}'

    @property
    def remaining(self):
        return self.amount - self.paid_amount

    def delete(self, *args, **kwargs):
        raise PermissionError('Начисления не удаляются — только отмена (ТЗ-02 п. 4.5)')


class BillingRun(TimestampedModel):
    """Сводка прогона начислений (ТЗ-02 п. 4.2): сохраняется и доступна в веб-панели."""
    run_date = models.DateField('Дата прогона')
    dry_run = models.BooleanField('Предварительный просмотр', default=False)
    created_count = models.PositiveIntegerField('Создано начислений', default=0)
    total_amount = MoneyField('Общая сумма', default=ZERO)
    skipped = models.JSONField('Пропущено (причины)', default=list)
    errors = models.JSONField('Ошибки', default=list)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Запустил',
        null=True, blank=True, on_delete=models.PROTECT,
        help_text='Пусто — запуск по расписанию')

    class Meta:
        verbose_name = 'Прогон начислений'
        verbose_name_plural = 'Прогоны начислений'
        ordering = ['-created_at']

    def __str__(self):
        kind = 'просмотр' if self.dry_run else 'прогон'
        return f'{kind} {self.run_date}: {self.created_count} на {self.total_amount}'
