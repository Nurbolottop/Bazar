from django.core.exceptions import ValidationError
from django.db import models

from apps.catalog.models import Spot
from apps.core.models import DAY_VALIDATORS, TimestampedModel
from apps.core.money import MoneyField


class Tenant(TimestampedModel):
    """Арендатор (ТЗ-02 п. 3.2).

    Отдельной учётной записи нет: вход в приложение выполняется по полю inn (FR-TN-02).
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Активный'
        SUSPENDED = 'suspended', 'Приостановлен'
        ARCHIVED = 'archived', 'Архивный'

    class Language(models.TextChoices):
        RU = 'ru', 'Русский'
        KY = 'ky', 'Кыргызский'

    full_name = models.CharField('ФИО', max_length=255)
    inn = models.CharField('ИНН', max_length=20, unique=True, db_index=True)
    passport_number = models.CharField('Номер паспорта', max_length=30, blank=True)
    phone = models.CharField('Телефон', max_length=30, blank=True)
    address = models.CharField('Адрес', max_length=255, blank=True)
    photo = models.ImageField('Фотография', upload_to='tenants/', blank=True, null=True)
    status = models.CharField(
        'Статус', max_length=10, choices=Status.choices, default=Status.ACTIVE)
    billing_day = models.PositiveSmallIntegerField(
        'Дата начисления', null=True, blank=True, validators=DAY_VALIDATORS,
        help_text='Пусто — берётся общая настройка')
    payment_day = models.PositiveSmallIntegerField(
        'Срок оплаты', null=True, blank=True, validators=DAY_VALIDATORS,
        help_text='Пусто — берётся общая настройка')
    language = models.CharField(
        'Язык интерфейса', max_length=2, choices=Language.choices, default=Language.RU)
    announcements_enabled = models.BooleanField('Получает объявления', default=True)
    consent_accepted_at = models.DateTimeField(
        'Согласие на обработку ПДн принято', null=True, blank=True)
    consent_version = models.CharField('Версия текста согласия', max_length=20, blank=True)
    pin_hash = models.CharField(
        'PIN-код (хеш)', max_length=128, blank=True,
        help_text='Используется только при включённой настройке pin_login_enabled')
    note = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Арендатор'
        verbose_name_plural = 'Арендаторы'
        ordering = ['full_name']

    def __str__(self):
        return f'{self.full_name} (ИНН {self.inn})'

    # Арендатор выступает субъектом аутентификации REST API (ТЗ-02 п. 3.2:
    # отдельной учётной записи нет — вход по полю inn)
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def clean(self):
        # FR-CH-09: дата начисления всегда строго раньше срока оплаты.
        # Сравниваются эффективные значения: индивидуальное либо общее.
        from apps.core.models import SystemSettings
        s = SystemSettings.load()
        billing = self.billing_day or s.default_billing_day
        payment = self.payment_day or s.default_payment_day
        if billing >= payment:
            raise ValidationError(
                'Дата начисления должна быть строго раньше срока оплаты '
                f'(получилось: начисление {billing}-го, срок {payment}-го). '
                'Иначе напоминания о сроке оплаты отправить невозможно.'
            )


class TenantSpot(TimestampedModel):
    """Привязка торгового места к арендатору с суммой аренды (ТЗ-02 п. 3.2)."""
    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.PROTECT, related_name='tenant_spots')
    spot = models.ForeignKey(
        Spot, verbose_name='Место', on_delete=models.PROTECT, related_name='tenant_spots')
    monthly_amount = MoneyField('Сумма аренды в месяц')
    start_date = models.DateField('Дата начала')
    end_date = models.DateField('Дата окончания', null=True, blank=True)
    is_active = models.BooleanField('Действует', default=True)

    class Meta:
        verbose_name = 'Место арендатора'
        verbose_name_plural = 'Места арендаторов'
        constraints = [
            # Одно место не может быть активно у двух арендаторов одновременно
            models.UniqueConstraint(
                fields=['spot'], condition=models.Q(is_active=True),
                name='uniq_active_tenant_per_spot'),
            models.CheckConstraint(
                check=models.Q(monthly_amount__gte=0), name='tenantspot_amount_gte_0'),
        ]

    def __str__(self):
        return f'{self.tenant.full_name} — {self.spot.code}'


class TenantDocument(TimestampedModel):
    """Документы арендатора (FR-TN-09)."""
    tenant = models.ForeignKey(
        Tenant, verbose_name='Арендатор', on_delete=models.PROTECT, related_name='documents')
    title = models.CharField('Название', max_length=255)
    file = models.FileField('Файл', upload_to='tenant_docs/')
    uploaded_at = models.DateTimeField('Загружен', auto_now_add=True)

    class Meta:
        verbose_name = 'Документ арендатора'
        verbose_name_plural = 'Документы арендаторов'

    def __str__(self):
        return self.title
