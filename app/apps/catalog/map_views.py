"""Интерактивная карта рынка: страница и JSON API.

Карта — визуальный слой над бизнес-сущностями (MapPosition над Spot).
Все мутации валидируются на сервере и пишутся в журнал действий.
Права: как у остальной панели — сотрудники администрации (is_staff);
скрытие кнопок на фронте защитой не считается, каждый endpoint проверяет сам.
"""
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.panel import admin_required, client_ip
from apps.core.services import audit

from .models import MapPosition, MarketPlan, Spot

MIN_SIZE = 16
MAX_SIZE = 800
DEFAULT_W = 80
DEFAULT_H = 50


@admin_required
def map_page(request):
    """Страница «Карта рынка». Данные подтягивает JS через map_plan_json."""
    return render(request, 'panel/spots_map.html', {
        'can_edit': request.user.is_staff,
    })


def _debtor_tenant_ids():
    from apps.core.money import ZERO
    from apps.payments.models import TenantBalance
    return set(TenantBalance.objects.filter(
        debt_amount__gt=ZERO).values_list('tenant_id', flat=True))


def _position_payload(position: MapPosition, debtors: set | None = None) -> dict:
    spot = position.spot
    data = {
        'id': position.pk,
        'x': position.x, 'y': position.y,
        'width': position.width, 'height': position.height,
        'updated_at': position.updated_at.isoformat(),
        'spot_id': spot.pk if spot else None,
        'code': spot.code if spot else None,
        'status': spot.status if spot else 'empty',
        'building': spot.building.name if spot else None,
        'tenant': None, 'tenant_id': None, 'has_debt': False,
    }
    if spot:
        link = next((ts for ts in spot.tenant_spots.all() if ts.is_active), None)
        if link:
            data['tenant'] = link.tenant.full_name
            data['tenant_id'] = link.tenant_id
            if debtors is not None:
                data['has_debt'] = link.tenant_id in debtors
    return data


@admin_required
def map_plan_json(request):
    """GET /map/api/plan/ — весь план одним запросом."""
    plan = MarketPlan.get_default()
    positions = MapPosition.objects.filter(plan=plan).select_related(
        'spot', 'spot__building').prefetch_related('spot__tenant_spots__tenant')
    debtors = _debtor_tenant_ids()
    placed_spot_ids = [p.spot_id for p in positions if p.spot_id]
    unplaced = Spot.objects.exclude(pk__in=placed_spot_ids) \
        .select_related('building').order_by('building__code', 'code')
    return JsonResponse({
        'plan': {
            'id': plan.pk, 'name': plan.name,
            'width': plan.width, 'height': plan.height,
            'background': plan.background.url if plan.background else None,
        },
        'positions': [_position_payload(p, debtors) for p in positions],
        'unplaced': [
            {'id': s.pk, 'code': s.code, 'building': s.building.name,
             'status': s.status}
            for s in unplaced
        ],
    })


def _json_body(request) -> dict:
    try:
        return json.loads(request.body.decode() or '{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def _validate_geometry(plan: MarketPlan, data: dict) -> dict | JsonResponse:
    try:
        x = float(data['x'])
        y = float(data['y'])
        width = float(data.get('width', DEFAULT_W))
        height = float(data.get('height', DEFAULT_H))
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'error': 'Неверные координаты.'}, status=400)
    if not (MIN_SIZE <= width <= MAX_SIZE and MIN_SIZE <= height <= MAX_SIZE):
        return JsonResponse(
            {'error': f'Размер места — от {MIN_SIZE} до {MAX_SIZE} единиц.'}, status=400)
    if x < 0 or y < 0 or x + width > plan.width or y + height > plan.height:
        return JsonResponse({'error': 'Место выходит за границы плана.'}, status=400)
    return {'x': x, 'y': y, 'width': width, 'height': height}


@admin_required
@require_POST
def position_create(request):
    """POST /map/api/positions/ — разместить существующий Spot на карте."""
    plan = MarketPlan.get_default()
    data = _json_body(request)
    geometry = _validate_geometry(plan, data)
    if isinstance(geometry, JsonResponse):
        return geometry
    spot = get_object_or_404(Spot, pk=data.get('spot_id'))
    if MapPosition.objects.filter(spot=spot).exists():
        return JsonResponse(
            {'error': f'Место {spot.code} уже размещено на карте.'}, status=409)
    try:
        position = MapPosition.objects.create(plan=plan, spot=spot, **geometry)
    except IntegrityError:
        return JsonResponse(
            {'error': f'Место {spot.code} уже размещено на карте.'}, status=409)
    audit(action='map_position_create', model_name='MapPosition', object_id=position.pk,
          actor=request.user, new_value={'spot': spot.code, **geometry},
          ip=client_ip(request))
    return JsonResponse(_position_payload(position, _debtor_tenant_ids()), status=201)


@admin_required
@require_http_methods(['PATCH'])
def position_update(request, pk: int):
    """PATCH /map/api/positions/<id>/ — перемещение и изменение размера."""
    plan = MarketPlan.get_default()
    data = _json_body(request)
    geometry = _validate_geometry(plan, data)
    if isinstance(geometry, JsonResponse):
        return geometry
    with transaction.atomic():
        position = get_object_or_404(
            MapPosition.objects.select_for_update().select_related(
                'spot', 'spot__building'), pk=pk)
        # Оптимистическая блокировка: другой администратор успел раньше — 409
        sent_version = data.get('updated_at')
        if sent_version and sent_version != position.updated_at.isoformat():
            return JsonResponse(
                {'error': 'Карта изменена другим пользователем. Страница будет обновлена.'},
                status=409)
        old = {'x': position.x, 'y': position.y,
               'width': position.width, 'height': position.height}
        for field, value in geometry.items():
            setattr(position, field, value)
        position.save()
        audit(action='map_position_update', model_name='MapPosition', object_id=pk,
              actor=request.user, old_value=old, new_value=geometry,
              ip=client_ip(request))
    return JsonResponse(_position_payload(position, _debtor_tenant_ids()))


@admin_required
@require_POST
def position_transfer(request):
    """POST /map/api/positions/transfer — swap двух мест либо перенос в пустую позицию.

    {source_id, target_id}: если target занята — обмен Spot между позициями,
    если пуста — перенос. Бизнес-данные (арендаторы, платежи) не затрагиваются.
    """
    data = _json_body(request)
    source_id, target_id = data.get('source_id'), data.get('target_id')
    if source_id == target_id:
        return JsonResponse({'error': 'Позиции совпадают.'}, status=400)
    with transaction.atomic():
        positions = {
            p.pk: p for p in MapPosition.objects.select_for_update()
            .filter(pk__in=[source_id, target_id])
        }
        source = positions.get(source_id)
        target = positions.get(target_id)
        if source is None or target is None:
            return JsonResponse({'error': 'Позиция не найдена.'}, status=404)
        if source.plan_id != target.plan_id:
            return JsonResponse({'error': 'Позиции на разных планах.'}, status=400)
        if source.spot_id is None:
            return JsonResponse({'error': 'Исходная позиция пуста.'}, status=400)

        old = {'source_spot': source.spot_id, 'target_spot': target.spot_id}
        source_spot_id, target_spot_id = source.spot_id, target.spot_id
        # OneToOne: сперва освобождаем обе, затем расставляем — иначе конфликт уникальности
        source.spot_id = None
        source.save(update_fields=['spot', 'updated_at'])
        target.spot_id = source_spot_id
        target.save(update_fields=['spot', 'updated_at'])
        if target_spot_id:
            source.spot_id = target_spot_id
            source.save(update_fields=['spot', 'updated_at'])
        action = 'map_position_swap' if target_spot_id else 'map_position_move'
        audit(action=action, model_name='MapPosition',
              object_id=f'{source.pk}->{target.pk}', actor=request.user,
              old_value=old,
              new_value={'source_spot': source.spot_id, 'target_spot': target.spot_id},
              ip=client_ip(request))
    debtors = _debtor_tenant_ids()
    source.refresh_from_db()
    target.refresh_from_db()
    return JsonResponse({
        'source': _position_payload(source, debtors),
        'target': _position_payload(target, debtors),
    })


@admin_required
@require_http_methods(['DELETE'])
def position_delete(request, pk: int):
    """DELETE /map/api/positions/<id>/delete — убрать размещение с карты.

    Само торговое место и вся его история остаются в системе.
    """
    position = get_object_or_404(MapPosition.objects.select_related('spot'), pk=pk)
    code = position.spot.code if position.spot else None
    audit(action='map_position_delete', model_name='MapPosition', object_id=pk,
          actor=request.user,
          old_value={'spot': code, 'x': position.x, 'y': position.y},
          ip=client_ip(request))
    position.delete()
    return JsonResponse({'deleted': pk, 'spot_code': code})
