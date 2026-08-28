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


class MarketPlan(TimestampedModel):
    """План рынка — холст интерактивной карты.

    Сейчас используется один активный план; модель допускает несколько
    (этажи, сектора). Подложка-чертёж опциональна: карта работает и без неё.
    """
    name = models.CharField('Название', max_length=100, default='Основной план')
    background = models.ImageField(
        'Подложка (чертёж)', upload_to='market_plans/', null=True, blank=True)
    width = models.FloatField('Ширина холста', default=2000)
    height = models.FloatField('Высота холста', default=1200)
    is_active = models.BooleanField('Действует', default=True)

    class Meta:
        verbose_name = 'План рынка'
        verbose_name_plural = 'Планы рынка'

    def __str__(self):
        return self.name

    @classmethod
    def get_default(cls) -> 'MarketPlan':
        plan = cls.objects.filter(is_active=True).order_by('id').first()
        if plan is None:
            plan = cls.objects.create()
        return plan


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


class MapPosition(TimestampedModel):
    """Визуальная позиция торгового места на плане рынка.

    Отдельный слой над бизнес-сущностями: Spot ничего не знает о карте.
    Позиция может быть пустой (spot=NULL) — например, после архивации места.
    Swap двух мест — обмен значениями spot между двумя позициями; арендаторы,
    платежи и договоры при этом не затрагиваются.
    """
    plan = models.ForeignKey(
        MarketPlan, verbose_name='План', on_delete=models.CASCADE, related_name='positions')
    spot = models.OneToOneField(
        Spot, verbose_name='Торговое место', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='map_position')
    x = models.FloatField('X')
    y = models.FloatField('Y')
    width = models.FloatField('Ширина')
    height = models.FloatField('Высота')
    rotation = models.FloatField('Поворот', default=0)
    shape = models.CharField('Форма', max_length=20, default='rect')

    class Meta:
        verbose_name = 'Позиция на карте'
        verbose_name_plural = 'Позиции на карте'
        constraints = [
            models.CheckConstraint(
                check=models.Q(width__gt=0) & models.Q(height__gt=0),
                name='mapposition_size_positive'),
        ]

    def __str__(self):
        who = self.spot.code if self.spot_id else 'пусто'
        return f'Позиция #{self.pk} ({who})'
