"""Панель: должники и отчёты с выгрузкой в Excel (ТЗ-00 п. 5.7)."""
import datetime
from decimal import Decimal

from django.shortcuts import render
from django.utils import timezone

from apps.catalog.models import Building
from apps.core.panel import admin_required

from . import services


def _period(request) -> tuple[datetime.date, datetime.date]:
    today = timezone.localdate()
    default_from = today.replace(day=1)
    try:
        date_from = datetime.date.fromisoformat(request.GET.get('from', ''))
    except ValueError:
        date_from = default_from
    try:
        date_to = datetime.date.fromisoformat(request.GET.get('to', ''))
    except ValueError:
        date_to = today
    return date_from, date_to


@admin_required
def debtors_view(request):
    building = request.GET.get('building', '')
    building_id = int(building) if building.isdigit() else None
    rows = services.debtors_report(building_id)

    if request.GET.get('export') == 'xlsx':
        return services.excel_response(
            'debtors.xlsx',
            ['Арендатор', 'ИНН', 'Телефон', 'Места', 'Долг, сом',
             'Дней просрочки', 'Последний платёж'],
            [[r['tenant'].full_name, r['tenant'].inn, r['phone'], r['spots'],
              r['debt'], r['days_overdue'],
              r['last_payment'].strftime('%d.%m.%Y') if r['last_payment'] else '—']
             for r in rows])

    total = sum((r['debt'] for r in rows), Decimal('0.00'))
    return render(request, 'panel/debtors.html', {
        'rows': rows, 'building': building,
        'buildings': Building.objects.filter(is_active=True),
        'total': total,
    })


@admin_required
def reports_view(request):
    date_from, date_to = _period(request)
    report = request.GET.get('report', 'payments')

    if report == 'charges':
        qs = services.charges_report(date_from, date_to)
        if request.GET.get('export') == 'xlsx':
            return services.excel_response(
                'charges.xlsx',
                ['Дата', 'Арендатор', 'ИНН', 'Место', 'Сумма', 'Срок оплаты', 'Статус'],
                [[c.charged_date.strftime('%d.%m.%Y'), c.tenant.full_name, c.tenant.inn,
                  c.tenant_spot.spot.code if c.tenant_spot else '—',
                  c.amount, c.due_date.strftime('%d.%m.%Y'), c.get_status_display()]
                 for c in qs])
        context_rows = qs[:500]
    elif report == 'free_spots':
        rows = services.free_spots_report()
        if request.GET.get('export') == 'xlsx':
            return services.excel_response(
                'free_spots.xlsx',
                ['Место', 'Корпус', 'Свободно с', 'Дней простоя'],
                [[r['spot'].code, r['spot'].building.name,
                  r['since'].strftime('%d.%m.%Y') if r['since'] else '—',
                  r['idle_days'] if r['idle_days'] is not None else '—']
                 for r in rows])
        context_rows = rows
    else:
        report = 'payments'
        qs = services.payments_report(date_from, date_to)
        if request.GET.get('export') == 'xlsx':
            return services.excel_response(
                'payments.xlsx',
                ['Дата', 'Арендатор', 'ИНН', 'Сумма', 'Статус', 'Кто подтвердил', 'Чек'],
                [[p.paid_at.strftime('%d.%m.%Y %H:%M'), p.tenant.full_name, p.tenant.inn,
                  p.amount, p.get_status_display(),
                  p.created_by.username if p.created_by else '—',
                  'есть' if p.claim_id else '—']
                 for p in qs])
        context_rows = qs[:500]

    return render(request, 'panel/reports.html', {
        'report': report, 'rows': context_rows,
        'date_from': date_from, 'date_to': date_to,
    })
