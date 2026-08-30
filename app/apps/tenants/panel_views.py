"""Панель: арендаторы, карточка, массовое редактирование сумм (ТЗ-02 п. 5.2)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.accounts.services import revoke_all_tokens
from apps.catalog.models import Spot
from apps.core.money import q2
from apps.core.panel import admin_required, client_ip, paginate
from apps.core.services import audit
from apps.payments.models import TenantBalance
from apps.payments.services import create_manual_payment

from . import services
from .models import Tenant, TenantSpot


TENANT_FIELDS = ['full_name', 'inn', 'passport_number', 'phone', 'address', 'note']


@admin_required
def tenants_list(request):
    """Список с поиском по ФИО, ИНН, телефону и номеру места (FR-TN-07)."""
    qs = Tenant.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) | Q(inn__icontains=q) |
            Q(phone__icontains=q) |
            Q(tenant_spots__spot__code__icontains=q, tenant_spots__is_active=True)
        ).distinct()
    status = request.GET.get('status', '')
    if status in dict(Tenant.Status.choices):
        qs = qs.filter(status=status)
    building = request.GET.get('building', '')
    if building.isdigit():
        qs = qs.filter(tenant_spots__is_active=True,
                       tenant_spots__spot__building_id=int(building)).distinct()
    debt = request.GET.get('debt', '')
    if debt == 'yes':
        qs = qs.filter(balance__debt_amount__gt=0)
    elif debt == 'no':
        qs = qs.exclude(balance__debt_amount__gt=0)

    page = paginate(request, qs.select_related('balance').order_by('full_name'))
    from apps.catalog.models import Building
    return render(request, 'panel/tenants_list.html', {
        'page': page, 'q': q, 'status': status, 'building': building, 'debt': debt,
        'statuses': Tenant.Status.choices,
        'buildings': Building.objects.filter(is_active=True),
    })


@admin_required
def tenant_form(request, pk: int | None = None):
    """Создание и редактирование карточки арендатора (FR-TN-01, FR-TN-02)."""
    tenant = get_object_or_404(Tenant, pk=pk) if pk else None
    if request.method == 'POST':
        data = {field: request.POST.get(field, '').strip() for field in TENANT_FIELDS}
        billing_day = request.POST.get('billing_day', '').strip()
        payment_day = request.POST.get('payment_day', '').strip()
        language = request.POST.get('language', 'ru')

        target = tenant or Tenant()
        old = {field: getattr(target, field) for field in TENANT_FIELDS} if tenant else None
        for field, value in data.items():
            setattr(target, field, value)
        target.billing_day = int(billing_day) if billing_day.isdigit() else None
        target.payment_day = int(payment_day) if payment_day.isdigit() else None
        if language in dict(Tenant.Language.choices):
            target.language = language
        if request.FILES.get('photo'):
            target.photo = request.FILES['photo']
        if request.FILES.get('passport_photo'):
            target.passport_photo = request.FILES['passport_photo']

        # Обязательные поля карточки: паспорт полностью (номер + копия) и телефон
        required_errors = []
        if not target.passport_number:
            required_errors.append('укажите номер паспорта')
        if not target.phone:
            required_errors.append('укажите номер телефона')
        if not target.passport_photo:
            required_errors.append('приложите копию/фото паспорта')
        if required_errors:
            messages.error(request, 'Заполните обязательные поля: ' + ', '.join(required_errors) + '.')
            return render(request, 'panel/tenant_form.html', {
                'tenant': target, 'is_new': tenant is None})
        try:
            target.full_clean()
        except ValidationError as exc:
            error_text = '; '.join(
                f'{field}: {", ".join(errs)}' for field, errs in exc.message_dict.items())
            messages.error(request, error_text)
            return render(request, 'panel/tenant_form.html', {
                'tenant': target, 'is_new': tenant is None})
        target.save()
        # Учётная запись создаётся автоматически: вход по ИНН (FR-TN-02)
        audit(action='tenant_update' if tenant else 'tenant_create',
              model_name='Tenant', object_id=target.pk, actor=request.user,
              old_value=old, new_value=data, ip=client_ip(request))
        messages.success(request, 'Карточка сохранена. Арендатор может войти в приложение по ИНН.')
        return redirect('panel:tenant_detail', pk=target.pk)
    return render(request, 'panel/tenant_form.html', {
        'tenant': tenant, 'is_new': tenant is None})


@admin_required
def tenant_detail(request, pk: int):
    """Карточка арендатора: места, долг, начисления, платежи, документы."""
    from apps.billing.models import Charge
    from apps.payments.models import DebtAdjustment, Payment, PaymentClaim

    tenant = get_object_or_404(Tenant, pk=pk)
    balance = TenantBalance.objects.filter(tenant=tenant).first()
    tenant_spots = tenant.tenant_spots.select_related('spot', 'spot__building') \
        .order_by('-is_active', 'spot__code')
    charges = tenant.charges.select_related('tenant_spot__spot').order_by('-due_date')[:30]
    payments = tenant.payments.select_related('claim').order_by('-paid_at')[:30]
    claims = tenant.claims.order_by('-submitted_at')[:15]
    adjustments = tenant.adjustments.order_by('-created_at')[:15]
    free_spots = Spot.objects.filter(status=Spot.Status.FREE).select_related('building')

    return render(request, 'panel/tenant_detail.html', {
        'tenant': tenant, 'balance': balance, 'tenant_spots': tenant_spots,
        'charges': charges, 'payments': payments, 'claims': claims,
        'adjustments': adjustments, 'free_spots': free_spots,
        'documents': tenant.documents.all(),
        'statuses': Tenant.Status.choices,
        'rental_categories': Tenant.RentalCategory.choices,
        'payment_types': Tenant.PaymentType.choices,
    })


@admin_required
@require_POST
def tenant_assign_spot(request, pk: int):
    tenant = get_object_or_404(Tenant, pk=pk)
    spot = get_object_or_404(Spot, pk=request.POST.get('spot_id'))
    try:
        amount = Decimal(str(request.POST.get('monthly_amount', '')).replace(',', '.'))
    except InvalidOperation:
        messages.error(request, 'Неверный формат суммы аренды.')
        return redirect('panel:tenant_detail', pk=pk)
    is_long_term = request.POST.get('is_long_term') == 'on'
    paid_raw = str(request.POST.get('paid_amount', '')).replace(',', '.').strip()
    try:
        paid_amount = Decimal(paid_raw) if paid_raw else Decimal('0')
    except InvalidOperation:
        messages.error(request, 'Неверный формат суммы «Оплачено».')
        return redirect('panel:tenant_detail', pk=pk)
    contract_until = parse_date(request.POST.get('contract_until', '') or '')
    try:
        services.assign_spot(
            tenant=tenant, spot=spot, monthly_amount=amount, actor=request.user,
            rental_category=request.POST.get('rental_category', 'self'),
            is_long_term=is_long_term, contract_until=contract_until,
            payment_type=request.POST.get('payment_type', ''),
            rents_from_market=request.POST.get('rents_from_market') == 'on')
        if is_long_term and paid_amount > 0:
            # «Оплачено» из модалки — реальный платёж: попадает в «Финансы → Платежи»
            # и гасит начисления (излишек остаётся авансом).
            create_manual_payment(
                tenant=tenant, amount=paid_amount, actor=request.user,
                comment=f'Оплата при привязке места {spot.code}')
            messages.success(
                request, f'Место {spot.code} привязано, платёж {q2(paid_amount)} сом внесён.')
        else:
            messages.success(request, f'Место {spot.code} привязано.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect('panel:tenant_detail', pk=pk)


@admin_required
@require_POST
def tenant_release_spot(request, pk: int, tenant_spot_id: int):
    tenant_spot = get_object_or_404(TenantSpot, pk=tenant_spot_id, tenant_id=pk)
    balance = TenantBalance.objects.filter(tenant_id=pk).first()
    services.release_spot(tenant_spot=tenant_spot, actor=request.user)
    if balance and balance.debt_amount > 0:
        # Система предупреждает, если за арендатором числится долг (ТЗ-00 п. 6.6)
        messages.warning(
            request, f'Место освобождено. Внимание: за арендатором долг {balance.debt_amount} сом.')
    else:
        messages.success(request, 'Место освобождено.')
    return redirect('panel:tenant_detail', pk=pk)


@admin_required
@require_POST
def tenant_set_status(request, pk: int):
    tenant = get_object_or_404(Tenant, pk=pk)
    status = request.POST.get('status')
    if status not in dict(Tenant.Status.choices):
        messages.error(request, 'Неизвестный статус.')
        return redirect('panel:tenant_detail', pk=pk)
    balance = TenantBalance.objects.filter(tenant=tenant).first()
    services.set_tenant_status(tenant=tenant, status=status, actor=request.user)
    if status == Tenant.Status.ARCHIVED and balance and balance.debt_amount > 0:
        messages.warning(
            request, f'Арендатор в архиве. Внимание: числится долг {balance.debt_amount} сом.')
    else:
        messages.success(request, 'Статус изменён.')
    return redirect('panel:tenant_detail', pk=pk)


@admin_required
@require_POST
def tenant_revoke_tokens(request, pk: int):
    tenant = get_object_or_404(Tenant, pk=pk)
    count = revoke_all_tokens(tenant)
    audit(action='tokens_revoke', model_name='Tenant', object_id=pk,
          actor=request.user, new_value={'revoked': count}, ip=client_ip(request))
    messages.success(request, f'Отозвано токенов: {count}.')
    return redirect('panel:tenant_detail', pk=pk)


@admin_required
@require_POST
def tenant_upload_document(request, pk: int):
    tenant = get_object_or_404(Tenant, pk=pk)
    file = request.FILES.get('file')
    title = request.POST.get('title', '').strip() or (file.name if file else '')
    if file:
        tenant.documents.create(title=title, file=file)
        messages.success(request, 'Документ загружен.')
    else:
        messages.error(request, 'Файл не выбран.')
    return redirect('panel:tenant_detail', pk=pk)


@admin_required
def mass_amounts(request):
    """Массовое редактирование сумм: правки в ячейках, сохранение одним действием (FR-CH-03)."""
    rows = TenantSpot.objects.filter(is_active=True) \
        .select_related('tenant', 'spot', 'spot__building') \
        .order_by('tenant__full_name', 'spot__code')

    if request.method == 'POST':
        changed = 0
        errors = []
        with transaction.atomic():
            for tenant_spot in rows.select_for_update(of=('self',)):
                raw = request.POST.get(f'amount_{tenant_spot.pk}')
                if raw is None:
                    continue
                try:
                    new_amount = q2(Decimal(str(raw).replace(',', '.').replace(' ', '')))
                except InvalidOperation:
                    errors.append(f'{tenant_spot.spot.code}: неверный формат «{raw}»')
                    continue
                if new_amount < 0:
                    errors.append(f'{tenant_spot.spot.code}: сумма не может быть отрицательной')
                    continue
                if new_amount != tenant_spot.monthly_amount:
                    old_amount = tenant_spot.monthly_amount
                    tenant_spot.monthly_amount = new_amount
                    tenant_spot.save(update_fields=['monthly_amount', 'updated_at'])
                    audit(action='amount_change', model_name='TenantSpot',
                          object_id=tenant_spot.pk, actor=request.user,
                          old_value={'monthly_amount': str(old_amount)},
                          new_value={'monthly_amount': str(new_amount)},
                          ip=client_ip(request))
                    changed += 1
        if errors:
            messages.error(request, 'Не сохранено: ' + '; '.join(errors))
        messages.success(request, f'Изменено сумм: {changed}.')
        return redirect('panel:mass_amounts')

    return render(request, 'panel/mass_amounts.html', {'rows': rows})
