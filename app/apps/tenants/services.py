"""Бизнес-логика арендаторов: привязка мест, статусы (ТЗ-00 п. 5.1–5.2, 6.6)."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Spot
from apps.core.money import q2
from apps.core.services import audit

from .models import Tenant, TenantSpot


def assign_spot(*, tenant: Tenant, spot: Spot, monthly_amount: Decimal,
                start_date: datetime.date | None = None, actor=None,
                rental_category: str = 'self',
                rental_term: str = 'long',
                rents_from_market: bool = False) -> TenantSpot:
    """Привязка места: состояние места автоматически становится «занято» (FR-SP-05).

    Категория аренды и признак «арендует у рынка» задаются на привязке:
    один арендатор может один контейнер вести сам, другой — пересдавать.
    """
    with transaction.atomic():
        spot = Spot.objects.select_for_update().get(pk=spot.pk)
        if TenantSpot.objects.filter(spot=spot, is_active=True).exists():
            raise ValidationError(f'Место {spot.code} уже занято другим арендатором.')
        if rental_category not in dict(Tenant.RentalCategory.choices):
            rental_category = Tenant.RentalCategory.SELF
        if rental_term not in dict(Tenant.RentalTerm.choices):
            rental_term = Tenant.RentalTerm.LONG
        tenant_spot = TenantSpot.objects.create(
            tenant=tenant, spot=spot, monthly_amount=q2(monthly_amount),
            rental_category=rental_category, rental_term=rental_term,
            rents_from_market=rents_from_market,
            start_date=start_date or timezone.localdate())
        spot.status = Spot.Status.OCCUPIED
        spot.save(update_fields=['status', 'updated_at'])
        audit(action='spot_assign', model_name='TenantSpot', object_id=tenant_spot.pk,
              actor=actor, new_value={
                  'tenant': tenant.inn, 'spot': spot.code,
                  'monthly_amount': str(tenant_spot.monthly_amount)})
    return tenant_spot


def release_spot(*, tenant_spot: TenantSpot, end_date: datetime.date | None = None,
                 actor=None) -> TenantSpot:
    """Отвязка места: состояние — «свободно», начисления прекращаются (ТЗ-00 п. 6.6)."""
    with transaction.atomic():
        tenant_spot = TenantSpot.objects.select_for_update().get(pk=tenant_spot.pk)
        if not tenant_spot.is_active:
            return tenant_spot
        tenant_spot.is_active = False
        tenant_spot.end_date = end_date or timezone.localdate()
        tenant_spot.save(update_fields=['is_active', 'end_date', 'updated_at'])
        spot = Spot.objects.select_for_update().get(pk=tenant_spot.spot_id)
        spot.status = Spot.Status.FREE
        spot.save(update_fields=['status', 'updated_at'])
        audit(action='spot_release', model_name='TenantSpot', object_id=tenant_spot.pk,
              actor=actor, new_value={'spot': spot.code})
    return tenant_spot


def set_tenant_status(*, tenant: Tenant, status: str, actor=None) -> Tenant:
    """Смена статуса арендатора (ТЗ-00 п. 5.2.1).

    При архивации отзываются все токены и освобождаются места; история сохраняется.
    """
    from apps.accounts.models import AuthToken

    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
        old_status = tenant.status
        if old_status == status:
            return tenant
        tenant.status = status
        tenant.save(update_fields=['status', 'updated_at'])

        if status == Tenant.Status.ARCHIVED:
            for token in AuthToken.objects.filter(tenant=tenant, revoked_at__isnull=True):
                token.revoke()
            for tenant_spot in tenant.tenant_spots.filter(is_active=True):
                release_spot(tenant_spot=tenant_spot, actor=actor)

        audit(action='tenant_status_change', model_name='Tenant', object_id=tenant.pk,
              actor=actor, old_value={'status': old_status}, new_value={'status': status})
    return tenant
