from django.db import models

from apps.core.models import TimestampedModel


class Building(TimestampedModel):
    """Корпус (ТЗ-02 п. 3.1)."""
    name = models.CharField('Название', max_length=100)
    code = models.CharField('Код', max_length=20, unique=True)
    description = models.TextField('Описание', blank=True)
    is_active = models.BooleanField('Действует', default=True)

    class Meta:
        verbose_name = 'Корпус'
        verbose_name_plural = 'Корпуса'
        ordering = ['code']

    def __str__(self):
        return self.name


class Spot(TimestampedModel):
    """Торговое место (ТЗ-02 п. 3.1, FR-SP-02)."""

    class Type(models.TextChoices):
        CONTAINER = 'container', 'Контейнер'
        PAVILION = 'pavilion', 'Павильон'
        COUNTER = 'counter', 'Прилавок'
        BOUTIQUE = 'boutique', 'Бутик'
        OTHER = 'other', 'Другое'

    class Status(models.TextChoices):
        FREE = 'free', 'Свободно'
        OCCUPIED = 'occupied', 'Занято'
        REPAIR = 'repair', 'На ремонте'

    building = models.ForeignKey(
        Building, verbose_name='Корпус', on_delete=models.PROTECT, related_name='spots')
    code = models.CharField('Код места', max_length=30, unique=True)
    spot_type = models.CharField(
        'Тип', max_length=20, choices=Type.choices, default=Type.CONTAINER)
    area_sqm = models.DecimalField(
        'Площадь, м²', max_digits=8, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        'Состояние', max_length=10, choices=Status.choices, default=Status.FREE)
    photo = models.ImageField('Фотография', upload_to='spots/', blank=True, null=True)
    note = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Торговое место'
        verbose_name_plural = 'Торговые места'
        ordering = ['building__code', 'code']

    def __str__(self):
        return f'{self.code} ({self.building.name})'
