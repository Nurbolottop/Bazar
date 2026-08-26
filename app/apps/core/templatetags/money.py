"""Формат сумм «12 500 сом» (ТЗ-00 п. 7.4). Подключён как builtin во всех шаблонах."""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(value):
    if value in (None, ''):
        return '0'
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return value
    text = f'{amount:,.2f}'.replace(',', ' ')
    return text.removesuffix('.00')


MONTHS_RU = ['', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
             'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']


@register.filter
def month_ru(value):
    """Название месяца по номеру: 8 → «Август»."""
    try:
        return MONTHS_RU[int(value)]
    except (ValueError, TypeError, IndexError):
        return value
