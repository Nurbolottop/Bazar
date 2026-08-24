"""Панель: заявки об оплате, платежи (ТЗ-02 п. 5.2–5.3)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.models import Charge
from apps.core.models import SystemSettings
from apps.core.panel import admin_required, paginate
from apps.tenants.models import Tenant

from . import services
from .models import Payment, PaymentClaim, TenantBalance


def _parse_amount(raw) -> Decimal | None:
    try:
        return Decimal(str(raw).replace(',', '.').replace(' ', ''))
    except (InvalidOperation, TypeError):
        return None


@admin_required
def claims_list(request):
    """Список заявок, по умолчанию фильтр «На проверке», старые — первыми."""
    status = request.GET.get('status', PaymentClaim.Status.PENDING)
    qs = PaymentClaim.objects.select_related('tenant').order_by('submitted_at')
    if status in dict(PaymentClaim.Status.choices):
        qs = qs.filter(status=status)
    elif status != 'all':
        status = 'all'
    page = paginate(request, qs)
    return render(request, 'panel/claims_list.html', {
        'page': page, 'status': status,
        'statuses': PaymentClaim.Status.choices,
        'reject_reasons': SystemSettings.load().reject_reasons,
    })


@admin_required
def claim_detail(request, pk: int):
    """Карточка заявки: чек в полном размере, долг и последние начисления арендатора."""
    claim = get_object_or_404(PaymentClaim.objects.select_related('tenant'), pk=pk)
    balance = TenantBalance.objects.filter(tenant=claim.tenant).first()
    last_charges = Charge.objects.filter(tenant=claim.tenant) \
        .exclude(status=Charge.Status.CANCELLED).order_by('-due_date')[:6]
    return render(request, 'panel/claim_detail.html', {
        'claim': claim, 'balance': balance, 'last_charges': last_charges,
        'reject_reasons': SystemSettings.load().reject_reasons,
    })


@admin_required
@require_POST
def claim_confirm(request, pk: int):
    claim = get_object_or_404(PaymentClaim, pk=pk)
    amount = None
    raw = request.POST.get('amount', '').strip()
    if raw:
        amount = _parse_amount(raw)
        if amount is None:
            messages.error(request, 'Неверный формат суммы.')
            return redirect('panel:claim_detail', pk=pk)
    try:
        services.confirm_claim(claim=claim, actor=request.user, amount=amount)
        messages.success(request, 'Заявка подтверждена, долг обновлён.')
    except services.ClaimAlreadyProcessed as exc:
        # Заявку уже обработал другой администратор (ТЗ-02 п. 5.3)
        messages.warning(request, str(exc))
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect(request.POST.get('next') or 'panel:claims')


@admin_required
@require_POST
def claim_reject(request, pk: int):
    claim = get_object_or_404(PaymentClaim, pk=pk)
    reason = request.POST.get('reason', '').strip() or request.POST.get('reason_custom', '').strip()
    try:
        services.reject_claim(claim=claim, actor=request.user, reason=reason)
        messages.success(request, 'Заявка отклонена, арендатору отправлено уведомление.')
    except services.ClaimAlreadyProcessed as exc:
        messages.warning(request, str(exc))
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect(request.POST.get('next') or 'panel:claims')


@admin_required
def payments_list(request):
    """Список подтверждённых платежей с фильтрами и возможностью отмены."""
    qs = Payment.objects.select_related('tenant', 'claim', 'created_by').order_by('-paid_at')
    status = request.GET.get('status', '')
    if status in dict(Payment.Status.choices):
        qs = qs.filter(status=status)
    search = request.GET.get('q', '').strip()
    if search:
        qs = qs.filter(tenant__full_name__icontains=search) | qs.filter(tenant__inn__icontains=search)
    page = paginate(request, qs)
    return render(request, 'panel/payments_list.html', {
        'page': page, 'status': status, 'q': search,
        'statuses': Payment.Status.choices,
    })


@admin_required
@require_POST
def payment_reverse(request, pk: int):
    payment = get_object_or_404(Payment, pk=pk)
    try:
        services.reverse_payment(
            payment=payment, actor=request.user,
            reason=request.POST.get('reason', '').strip())
        messages.success(request, 'Платёж отменён, долг восстановлен.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect(request.POST.get('next') or 'panel:payments')


@admin_required
@require_POST
def tenant_manual_payment(request, tenant_id: int):
    """Внесение платежа за арендатора напрямую (FR-PM-10)."""
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    amount = _parse_amount(request.POST.get('amount'))
    if amount is None:
        messages.error(request, 'Неверный формат суммы.')
        return redirect('panel:tenant_detail', pk=tenant_id)
    try:
        services.create_manual_payment(
            tenant=tenant, amount=amount, actor=request.user,
            comment=request.POST.get('comment', '').strip())
        messages.success(request, 'Платёж внесён.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect('panel:tenant_detail', pk=tenant_id)


@admin_required
@require_POST
def tenant_adjust_debt(request, tenant_id: int):
    """Ручная корректировка долга (FR-PM-15)."""
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    amount = _parse_amount(request.POST.get('amount'))
    if amount is None:
        messages.error(request, 'Неверный формат суммы.')
        return redirect('panel:tenant_detail', pk=tenant_id)
    if request.POST.get('direction') == 'minus' and amount > 0:
        amount = -amount
    try:
        services.adjust_debt(
            tenant=tenant, amount=amount, actor=request.user,
            reason=request.POST.get('reason', '').strip())
        messages.success(request, 'Корректировка проведена.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect('panel:tenant_detail', pk=tenant_id)
