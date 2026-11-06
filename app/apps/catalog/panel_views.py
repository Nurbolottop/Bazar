"""Панель: корпуса и торговые места (FR-SP-01..06)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.core.panel import admin_required, client_ip, paginate
from apps.core.services import audit

from .models import Building, Spot


@admin_required
def spots_table(request):
    """Список мест с поиском и фильтрами по корпусу и состоянию."""
    qs = Spot.objects.select_related('building').prefetch_related('tenant_spots__tenant')
    building = request.GET.get('building', '')
    if building.isdigit():
        qs = qs.filter(building_id=int(building))
    status = request.GET.get('status', '')
    if status in dict(Spot.Status.choices):
        qs = qs.filter(status=status)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(note__icontains=q))

    page = paginate(request, qs.order_by('building__code', 'code'))
    return render(request, 'panel/spots_table.html', {
        'page': page, 'building': building, 'status': status, 'q': q,
        'buildings': Building.objects.all(),
        'statuses': Spot.Status.choices,
    })


@admin_required
def spots_manage(request):
    """Добавление корпусов и мест: поштучно и массово."""
    return render(request, 'panel/spots_manage.html', {
        'buildings': Building.objects.all(),
        'types': Spot.Type.choices,
    })


@admin_required
@require_POST
def building_create(request):
    name = request.POST.get('name', '').strip()
    code = request.POST.get('code', '').strip()
    if not name or not code:
        messages.error(request, 'Укажите название и код корпуса.')
    elif Building.objects.filter(code=code).exists():
        messages.error(request, f'Корпус с кодом {code} уже существует.')
    else:
        building = Building.objects.create(name=name, code=code)
        audit(action='building_create', model_name='Building', object_id=building.pk,
              actor=request.user, new_value={'name': name, 'code': code},
              ip=client_ip(request))
        messages.success(request, f'Корпус {name} создан.')
    return redirect('panel:spots_manage')


@admin_required
@require_POST
def spot_create(request):
    building = get_object_or_404(Building, pk=request.POST.get('building_id'))
    code = request.POST.get('code', '').strip()
    if not code:
        messages.error(request, 'Укажите код места.')
    elif Spot.objects.filter(code=code).exists():
        messages.error(request, f'Место с кодом {code} уже существует.')
    else:
        spot_type = request.POST.get('spot_type', Spot.Type.CONTAINER)
        area = request.POST.get('area_sqm', '').strip().replace(',', '.')
        spot = Spot.objects.create(
            building=building, code=code,
            spot_type=spot_type if spot_type in dict(Spot.Type.choices) else Spot.Type.CONTAINER,
            area_sqm=area or None,
            note=request.POST.get('note', '').strip())
        audit(action='spot_create', model_name='Spot', object_id=spot.pk,
              actor=request.user, new_value={'code': code}, ip=client_ip(request))
        messages.success(request, f'Место {code} создано.')
    return redirect('panel:spots_manage')


@admin_required
@require_POST
def spots_mass_create(request):
    """Массовое создание мест по шаблону с автонумерацией (FR-SP-03)."""
    building = get_object_or_404(Building, pk=request.POST.get('building_id'))
    prefix = request.POST.get('prefix', '').strip()
    try:
        start = int(request.POST.get('start', 1))
        count = int(request.POST.get('count', 0))
    except ValueError:
        messages.error(request, 'Номер и количество должны быть числами.')
        return redirect('panel:spots_manage')
    if count < 1 or count > 500:
        messages.error(request, 'Количество мест — от 1 до 500.')
        return redirect('panel:spots_manage')

    spot_type = request.POST.get('spot_type', Spot.Type.CONTAINER)
    created, skipped = 0, []
    for i in range(start, start + count):
        code = f'{prefix}{i:02d}' if prefix else f'{building.code}-{i:02d}'
        if Spot.objects.filter(code=code).exists():
            skipped.append(code)
            continue
        Spot.objects.create(
            building=building, code=code,
            spot_type=spot_type if spot_type in dict(Spot.Type.choices) else Spot.Type.CONTAINER)
        created += 1
    audit(action='spots_mass_create', model_name='Spot', object_id=building.pk,
          actor=request.user,
          new_value={'created': created, 'skipped': skipped}, ip=client_ip(request))
    text = f'Создано мест: {created}.'
    if skipped:
        text += f' Пропущены занятые коды: {", ".join(skipped[:10])}.'
    messages.success(request, text)
    return redirect('panel:spots_manage')


@admin_required
def spot_history(request, pk: int):
    """История: какие арендаторы и в какие периоды занимали место (FR-SP-06).

    Отсюда же можно привязать арендатора к месту (существующего или нового).
    """
    from apps.tenants.models import Tenant

    spot = get_object_or_404(Spot.objects.select_related('building'), pk=pk)
    history = spot.tenant_spots.select_related('tenant').order_by('-start_date')
    active_link = history.filter(is_active=True).first()
    tenants = None
    if active_link is None:
        tenants = Tenant.objects.exclude(
            status=Tenant.Status.ARCHIVED).order_by('full_name')
    return render(request, 'panel/spot_history.html', {
        'spot': spot, 'history': history, 'active_link': active_link,
        'tenants': tenants,
        'rental_categories': Tenant.RentalCategory.choices,
        'payment_types': Tenant.PaymentType.choices,
    })


@admin_required
@require_POST
def spot_assign(request, pk: int):
    """Привязать арендатора к месту прямо с карточки места (FR-SP-05).

    mode=existing — выбрать из списка; mode=new — создать нового и привязать.
    Расчёт и смена состояния места — в сервисном слое (assign_spot).
    """
    from apps.tenants import services as tenant_services
    from apps.tenants.models import Tenant

    spot = get_object_or_404(Spot, pk=pk)

    if request.POST.get('mode') == 'new':
        full_name = request.POST.get('full_name', '').strip()
        inn = request.POST.get('inn', '').strip()
        phone = request.POST.get('phone', '').strip()
        if not full_name or not inn:
            messages.error(request, 'Для нового арендатора укажите ФИО и ИНН.')
            return redirect('panel:spot_history', pk=pk)
        if Tenant.objects.filter(inn=inn).exists():
            messages.error(
                request, f'Арендатор с ИНН {inn} уже есть — выберите его из списка.')
            return redirect('panel:spot_history', pk=pk)
        tenant = Tenant.objects.create(full_name=full_name, inn=inn, phone=phone)
        audit(action='tenant_create', model_name='Tenant', object_id=tenant.pk,
              actor=request.user, new_value={'inn': inn, 'full_name': full_name},
              ip=client_ip(request))
    else:
        tenant = get_object_or_404(Tenant, pk=request.POST.get('tenant_id'))

    try:
        amount = Decimal(str(request.POST.get('monthly_amount', '')).replace(',', '.'))
    except InvalidOperation:
        messages.error(request, 'Неверный формат суммы аренды.')
        return redirect('panel:spot_history', pk=pk)

    is_long_term = request.POST.get('is_long_term') == 'on'
    try:
        tenant_services.assign_spot(
            tenant=tenant, spot=spot, monthly_amount=amount, actor=request.user,
            start_date=parse_date(request.POST.get('start_date', '') or ''),
            rental_category=request.POST.get('rental_category', 'self'),
            is_long_term=is_long_term,
            contract_until=parse_date(request.POST.get('contract_until', '') or ''),
            payment_type=request.POST.get('payment_type', ''),
            rents_from_market=request.POST.get('rents_from_market') == 'on')
        messages.success(
            request, f'Арендатор {tenant.full_name} привязан к месту {spot.code}.')
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    return redirect('panel:spot_history', pk=pk)
