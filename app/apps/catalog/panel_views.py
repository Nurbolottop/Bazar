"""Панель: корпуса и торговые места (FR-SP-01..06)."""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.panel import admin_required, client_ip, paginate
from apps.core.services import audit

from .models import Building, Spot


@admin_required
def spots_map(request):
    """Карта рынка: корпуса-сектора с квадратами мест. Главная страница раздела."""
    from django.db.models import Prefetch
    from apps.tenants.models import TenantSpot

    map_buildings = []
    buildings_qs = Building.objects.filter(is_active=True).prefetch_related(
        Prefetch('spots', queryset=Spot.objects.order_by('code').prefetch_related(
            Prefetch('tenant_spots',
                     queryset=TenantSpot.objects.filter(is_active=True).select_related('tenant'),
                     to_attr='active_links'))),
    ).order_by('code')
    for b in buildings_qs:
        spots = []
        occupied = 0
        for spot in b.spots.all():
            link = spot.active_links[0] if spot.active_links else None
            if spot.status == Spot.Status.OCCUPIED:
                occupied += 1
            spots.append({'spot': spot, 'link': link})
        map_buildings.append({
            'building': b, 'spots': spots,
            'total': len(spots), 'occupied': occupied,
        })
    return render(request, 'panel/spots_map.html', {'map_buildings': map_buildings})


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
    """История: какие арендаторы и в какие периоды занимали место (FR-SP-06)."""
    spot = get_object_or_404(Spot.objects.select_related('building'), pk=pk)
    history = spot.tenant_spots.select_related('tenant').order_by('-start_date')
    return render(request, 'panel/spot_history.html', {'spot': spot, 'history': history})
