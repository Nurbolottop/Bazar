"""Панель: начисления, прогон и предварительный просмотр (ТЗ-02 п. 5.2)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Building
from apps.core.panel import admin_required, paginate
from apps.tenants.models import Tenant

from . import services
from .models import BillingRun, Charge


@admin_required
def charges_list(request):
    """Список начислений с фильтрами по месяцу, корпусу и статусу."""
    qs = Charge.objects.select_related('tenant', 'tenant_spot__spot').order_by('-due_date', '-id')
    period = request.GET.get('period', '')
    if period:
        try:
            year, month = period.split('-')
            qs = qs.filter(period_year=int(year), period_month=int(month))
        except (ValueError, TypeError):
            period = ''
    status = request.GET.get('status', '')
    if status in dict(Charge.Status.choices):
        qs = qs.filter(status=status)
    building = request.GET.get('building', '')
    if building.isdigit():
        qs = qs.filter(tenant_spot__spot__building_id=int(building))

    page = paginate(request, qs)
    runs = BillingRun.objects.order_by('-created_at')[:5]
    return render(request, 'panel/charges_list.html', {
        'page': page, 'period': period, 'status': status, 'building': building,
        'statuses': Charge.Status.choices,
        'buildings': Building.objects.filter(is_active=True),
        'runs': runs,
    })


@admin_required
@require_POST
def billing_preview(request):
    """Предварительный просмотр прогона: кому, сколько, на какую сумму (FR-CH-07)."""
    run = services.run_billing(dry_run=True, started_by=request.user)
    return render(request, 'panel/billing_preview.html', {
        'run': run, 'preview': getattr(run, 'preview', []),
    })


@admin_required
@require_POST
def billing_run(request):
    """Ручной запуск прогона начислений из веб-панели (ТЗ-02 раздел 8)."""
    run = services.run_billing(started_by=request.user)
    messages.success(
        request,
        f'Прогон выполнен: создано {run.created_count} начислений '
        f'на {run.total_amount} сом, пропущено {len(run.skipped)}, ошибок {len(run.errors)}.')
    return redirect('panel:charges')


@admin_required
@require_POST
def charge_cancel(request, pk: int):
    charge = get_object_or_404(Charge, pk=pk)
    try:
        services.cancel_charge(
            charge, reason=request.POST.get('reason', '').strip(), actor=request.user)
        messages.success(request, 'Начисление отменено.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect(request.POST.get('next') or 'panel:charges')


@admin_required
@require_POST
def charge_create_manual(request, tenant_id: int):
    """Разовое начисление с произвольной суммой и комментарием (FR-CH-04)."""
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    try:
        amount = Decimal(str(request.POST.get('amount', '')).replace(',', '.'))
    except InvalidOperation:
        messages.error(request, 'Неверный формат суммы.')
        return redirect('panel:tenant_detail', pk=tenant_id)
    try:
        services.create_manual_charge(
            tenant=tenant, amount=amount,
            comment=request.POST.get('comment', '').strip(), actor=request.user)
        messages.success(request, 'Начисление создано.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect('panel:tenant_detail', pk=tenant_id)
