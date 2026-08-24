"""Работа с денежными суммами: только Decimal, ROUND_HALF_UP (ТЗ-02 п. 4.7)."""
from decimal import Decimal, ROUND_HALF_UP

from django.db import models

TWO_PLACES = Decimal('0.01')
ZERO = Decimal('0.00')


def q2(value) -> Decimal:
    """Приведение суммы к двум знакам после запятой, ROUND_HALF_UP."""
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def MoneyField(verbose_name: str, **kwargs) -> models.DecimalField:
    """DecimalField(12, 2) — единственный допустимый тип для сумм (ТЗ-02 п. 2.1)."""
    kwargs.setdefault('max_digits', 12)
    kwargs.setdefault('decimal_places', 2)
    return models.DecimalField(verbose_name, **kwargs)
