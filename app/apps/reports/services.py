"""Отчёты (ТЗ-00 п. 5.7) и выгрузка в Excel (FR-RP-06)."""
import datetime
import io

from django.db.models import Max, Q
from django.http import HttpResponse
from django.utils import timezone

from apps.billing.models import Charge
from apps.catalog.models import Spot
from apps.core.money import ZERO
from apps.payments.models import Payment, TenantBalance
from apps.tenants.models import Tenant, TenantSpot


def debtors_report(building_id: int | None = None) -> list[dict]:
    """Список должников (FR-RP-02): долг, дни просрочки, последний платёж."""
    today = timezone.localdate()
    balances = TenantBalance.objects.filter(debt_amount__gt=ZERO) \
        .select_related('tenant').order_by('-debt_amount')
    rows = []
    for balance in balances:
        tenant = balance.tenant
        spots_qs = tenant.tenant_spots.filter(is_active=True).select_related('spot')
        if building_id:
            if not spots_qs.filter(spot__building_id=building_id).exists():
                continue
        oldest_overdue = tenant.charges.filter(
            status=Charge.Status.OVERDUE).order_by('due_date').first()
        days_overdue = (today - oldest_overdue.due_date).days if oldest_overdue else 0
        last_payment = tenant.payments.filter(status=Payment.Status.ACTIVE) \
            .aggregate(last=Max('paid_at'))['last']
        rows.append({
            'tenant': tenant,
            'spots': ', '.join(ts.spot.code for ts in spots_qs),
            'debt': balance.debt_amount,
            'days_overdue': days_overdue,
            'last_payment': last_payment,
            'phone': tenant.phone,
        })
    return rows


def payments_report(date_from: datetime.date, date_to: datetime.date):
    """Платежи за период (FR-RP-03)."""
    return Payment.objects.filter(
        paid_at__date__gte=date_from, paid_at__date__lte=date_to,
    ).select_related('tenant', 'created_by', 'claim').order_by('paid_at')


def charges_report(date_from: datetime.date, date_to: datetime.date):
    """Начисления за период (FR-RP-04)."""
    return Charge.objects.filter(
        charged_date__gte=date_from, charged_date__lte=date_to,
    ).select_related('tenant', 'tenant_spot__spot').order_by('charged_date')


def free_spots_report() -> list[dict]:
    """Свободные места (FR-RP-05): место, корпус, срок простоя."""
    today = timezone.localdate()
    rows = []
    for spot in Spot.objects.filter(status=Spot.Status.FREE).select_related('building'):
        last_end = spot.tenant_spots.filter(is_active=False) \
            .aggregate(last=Max('end_date'))['last']
        idle_days = (today - last_end).days if last_end else None
        rows.append({'spot': spot, 'idle_days': idle_days, 'since': last_end})
    return rows


def excel_response(filename: str, header: list[str], rows: list[list]) -> HttpResponse:
    """Любой отчёт выгружается в Excel (FR-RP-06)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(header)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    for column_cells in sheet.columns:
        width = max((len(str(c.value)) for c in column_cells if c.value is not None), default=10)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(width + 2, 50)

    buffer = io.BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
