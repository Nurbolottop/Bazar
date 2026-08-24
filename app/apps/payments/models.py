from django.conf import settings
from django.db import models

from apps.billing.models import Charge
from apps.core.models import TimestampedModel
from apps.core.money import MoneyField, ZERO
from apps.tenants.models import Tenant


class PaymentClaim(TimestampedModel):
    """Заявка об оплате (ТЗ-02 п. 3.3). Долг арендатора не меняет (FR-PM-02)."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'На проверке'
        CONFIRMED = 'confirmed', 'Подтверждена'
        REJECTED = 'rejected', 'Отклонена'
        WITHDRAWN = 'withdrawn', 'Отозвана'

    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.PROTECT, related_name='claims')
    declared_amount = MoneyField('Заявленная сумма')
    receipt_image = models.ImageField('Фотография чека', upload_to='receipts/%Y/%m/')
    comment = models.TextField('Комментарий', blank=True)
    status = models.CharField(
        'Статус', max_length=10, choices=Status.choices, default=Status.PENDING)
    submitted_at = models.DateTimeField('Подана', auto_now_add=True)
    reviewed_at = models.DateTimeField('Обработана', null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто обработал',
        null=True, blank=True, on_delete=models.PROTECT, related_name='reviewed_claims')
    reject_reason = models.TextField('Причина отклонения', blank=True)
    idempotency_key = models.CharField('Ключ идемпотентности', max_length=64, unique=True)
    device_info = models.CharField('Устройство', max_length=255, blank=True)

    class Meta:
        verbose_name = 'Заявка об оплате'
        verbose_name_plural = 'Заявки об оплате'
        ordering = ['submitted_at']
        indexes = [
            models.Index(fields=['status', 'submitted_at']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(declared_amount__gte=1), name='claim_amount_gte_1'),
        ]

    def __str__(self):
        return f'Заявка #{self.pk} {self.tenant.full_name} на {self.declared_amount}'


class Payment(TimestampedModel):
    """Платёж (ТЗ-02 п. 3.3). Не удаляется — только сторно (status=reversed)."""

    class Source(models.TextChoices):
        CLAIM = 'claim', 'По заявке'
        MANUAL = 'manual', 'Внесён администратором'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Действует'
        REVERSED = 'reversed', 'Отменён (сторно)'

    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.PROTECT, related_name='payments')
    amount = MoneyField('Сумма')
    paid_at = models.DateTimeField('Дата платежа')
    source = models.CharField('Происхождение', max_length=10, choices=Source.choices)
    claim = models.ForeignKey(
        PaymentClaim, verbose_name='Заявка', null=True, blank=True,
        on_delete=models.PROTECT, related_name='payments')
    status = models.CharField(
        'Статус', max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто внёс/подтвердил',
        null=True, blank=True, on_delete=models.PROTECT, related_name='created_payments')
    reversed_reason = models.TextField('Причина отмены', blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто отменил',
        null=True, blank=True, on_delete=models.PROTECT, related_name='reversed_payments')
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Платёж'
        verbose_name_plural = 'Платежи'
        ordering = ['paid_at', 'id']
        indexes = [
            models.Index(fields=['tenant', 'paid_at']),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name='payment_amount_gt_0'),
        ]

    def __str__(self):
        return f'Платёж #{self.pk} {self.tenant.full_name} {self.amount}'

    def delete(self, *args, **kwargs):
        raise PermissionError('Платежи не удаляются — только сторно (ТЗ-02 п. 4.5)')


class PaymentAllocation(TimestampedModel):
    """Распределение платежа по начислениям (ТЗ-02 п. 3.3).

    kind=payment — прямое распределение платежа;
    kind=advance — зачёт аванса в новое начисление со ссылкой на исходный
      платёж-переплату (payment может быть пустым, если аванс образован
      корректировкой долга);
    kind=adjustment — погашение начисления корректировкой долга в минус.
    Записи не удаляются: при отмене переводятся в статус reversed.
    """

    class Kind(models.TextChoices):
        PAYMENT = 'payment', 'Платёж'
        ADVANCE = 'advance', 'Зачёт аванса'
        ADJUSTMENT = 'adjustment', 'Корректировка долга'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Действует'
        REVERSED = 'reversed', 'Отменено'

    payment = models.ForeignKey(
        Payment, verbose_name='Платёж', null=True, blank=True,
        on_delete=models.PROTECT, related_name='allocations')
    adjustment = models.ForeignKey(
        'DebtAdjustment', verbose_name='Корректировка', null=True, blank=True,
        on_delete=models.PROTECT, related_name='allocations')
    charge = models.ForeignKey(
        Charge, verbose_name='Начисление', on_delete=models.PROTECT, related_name='allocations')
    amount = MoneyField('Сумма')
    kind = models.CharField('Вид', max_length=12, choices=Kind.choices)
    status = models.CharField(
        'Статус', max_length=10, choices=Status.choices, default=Status.ACTIVE)
    reversed_at = models.DateTimeField('Отменено', null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Кто отменил',
        null=True, blank=True, on_delete=models.PROTECT, related_name='reversed_allocations')

    class Meta:
        verbose_name = 'Распределение платежа'
        verbose_name_plural = 'Распределения платежей'
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name='allocation_amount_gt_0'),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} {self.amount} → начисление #{self.charge_id}'

    def delete(self, *args, **kwargs):
        raise PermissionError('Распределения не удаляются — только сторно (ТЗ-02 п. 3.3)')


class DebtAdjustment(TimestampedModel):
    """Ручная корректировка долга (FR-PM-15).

    amount > 0 — долг увеличивается (создаётся связанное начисление source=adjustment);
    amount < 0 — долг уменьшается: погашаются начисления от ранних к поздним,
    излишек уходит в аванс (advance_excess).
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Действует'
        REVERSED = 'reversed', 'Отменена'

    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.PROTECT, related_name='adjustments')
    amount = MoneyField('Сумма (плюс или минус)')
    reason = models.TextField('Причина')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Автор',
        null=True, blank=True, on_delete=models.PROTECT, related_name='debt_adjustments')
    status = models.CharField(
        'Статус', max_length=10, choices=Status.choices, default=Status.ACTIVE)
    charge = models.OneToOneField(
        Charge, verbose_name='Начисление корректировки', null=True, blank=True,
        on_delete=models.PROTECT, related_name='adjustment',
        help_text='Для корректировки в плюс')
    advance_excess = MoneyField(
        'Излишек, зачисленный в аванс', default=ZERO,
        help_text='Для корректировки в минус, превысившей долг')

    class Meta:
        verbose_name = 'Корректировка долга'
        verbose_name_plural = 'Корректировки долга'
        ordering = ['-created_at']

    def __str__(self):
        return f'Корректировка {self.amount} ({self.tenant.full_name})'


class TenantBalance(models.Model):
    """Баланс арендатора (ТЗ-02 п. 3.3).

    Значения производные: пересчитываются сервисом recalc_balance внутри той же
    транзакции, что и изменение начислений или платежей (ТЗ-02 п. 3.5).
    """
    tenant = models.OneToOneField(
        Tenant, verbose_name='Арендатор', on_delete=models.CASCADE, related_name='balance')
    debt_amount = MoneyField('Долг', default=ZERO)
    advance_amount = MoneyField('Аванс', default=ZERO)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Баланс арендатора'
        verbose_name_plural = 'Балансы арендаторов'

    def __str__(self):
        return f'{self.tenant.full_name}: долг {self.debt_amount}, аванс {self.advance_amount}'
