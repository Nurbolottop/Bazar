"""Общие фабрики для тестов."""
import datetime
import io
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.catalog.models import Building, Spot
from apps.tenants.models import Tenant, TenantSpot


def make_admin(username='admin') -> User:
    return User.objects.create_user(username=username, password='x' * 12, is_staff=True)


def make_building(code='A') -> Building:
    return Building.objects.create(name=f'Корпус {code}', code=code)


def make_spot(building=None, code='A-01') -> Spot:
    building = building or make_building(code=f'B{code}')
    return Spot.objects.create(building=building, code=code)


_counter = iter(range(10_000, 1_000_000))


def make_tenant(inn=None, status=Tenant.Status.ACTIVE, **kwargs) -> Tenant:
    inn = inn or str(next(_counter))
    return Tenant.objects.create(full_name=f'Арендатор {inn}', inn=inn, status=status, **kwargs)


def make_tenant_with_spot(monthly_amount='12000.00', inn=None, spot_code=None,
                          start_date=None) -> tuple[Tenant, TenantSpot]:
    tenant = make_tenant(inn=inn)
    spot = make_spot(code=spot_code or f'S-{tenant.inn}')
    tenant_spot = TenantSpot.objects.create(
        tenant=tenant, spot=spot, monthly_amount=Decimal(monthly_amount),
        start_date=start_date or datetime.date(2026, 1, 1))
    return tenant, tenant_spot


def png_upload(name='receipt.png') -> SimpleUploadedFile:
    """Настоящий PNG размером 1×1 — проходит проверку сигнатуры."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (1, 1), 'white').save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')
