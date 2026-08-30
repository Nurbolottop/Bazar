"""Тесты интерактивной карты рынка: API позиций, swap, права, валидация."""
import json

from django.test import Client, TestCase

from apps.catalog.models import MapPosition, MarketPlan, Spot
from apps.core.models import AuditLog
from apps.core.testutils import make_admin, make_building, make_tenant_with_spot


class MapApiTests(TestCase):
    def setUp(self):
        self.admin = make_admin('chief')
        self.client_web = Client()
        self.client_web.post('/login/', {'username': 'chief', 'password': 'x' * 12})
        self.plan = MarketPlan.get_default()
        building = make_building('MAP')
        self.spot_a = Spot.objects.create(building=building, code='M-01')
        self.spot_b = Spot.objects.create(building=building, code='M-02')

    def _create(self, spot, x=100, y=100, **kwargs):
        payload = {'spot_id': spot.pk, 'x': x, 'y': y, 'width': 80, 'height': 50}
        payload.update(kwargs)
        return self.client_web.post(
            '/map/api/positions/', json.dumps(payload), content_type='application/json')

    def test_page_and_plan_json(self):
        response = self.client_web.get('/spots/')
        self.assertContains(response, 'map-canvas')
        response = self.client_web.get('/map/api/plan/')
        data = response.json()
        self.assertEqual(data['plan']['width'], 2000)
        self.assertEqual(len(data['positions']), 0)
        codes = [s['code'] for s in data['unplaced']]
        self.assertIn('M-01', codes)

    def test_create_position_and_persistence(self):
        """Создание позиции; после «перезагрузки» (нового запроса) координаты те же."""
        response = self._create(self.spot_a, x=150, y=90)
        self.assertEqual(response.status_code, 201)
        data = self.client_web.get('/map/api/plan/').json()
        self.assertEqual(len(data['positions']), 1)
        position = data['positions'][0]
        self.assertEqual((position['x'], position['y']), (150, 90))
        self.assertEqual(position['code'], 'M-01')
        # размещённое место ушло из списка неразмещённых
        self.assertNotIn('M-01', [s['code'] for s in data['unplaced']])

    def test_spot_cannot_be_placed_twice(self):
        self._create(self.spot_a)
        response = self._create(self.spot_a, x=300, y=300)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(MapPosition.objects.count(), 1)

    def test_move_and_resize(self):
        position = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=10, y=10, width=80, height=50)
        response = self.client_web.patch(
            f'/map/api/positions/{position.pk}/',
            json.dumps({'x': 400, 'y': 200, 'width': 120, 'height': 60,
                        'updated_at': position.updated_at.isoformat()}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        position.refresh_from_db()
        self.assertEqual((position.x, position.y), (400, 200))
        self.assertEqual((position.width, position.height), (120, 60))
        self.assertTrue(AuditLog.objects.filter(action='map_position_update').exists())

    def test_geometry_validation(self):
        position = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=10, y=10, width=80, height=50)
        for bad in [
            {'x': -5, 'y': 10, 'width': 80, 'height': 50},          # за границей
            {'x': 1990, 'y': 10, 'width': 80, 'height': 50},        # вылезает справа
            {'x': 10, 'y': 10, 'width': 5, 'height': 50},           # слишком маленькое
            {'x': 10, 'y': 10, 'width': 2000, 'height': 50},        # слишком большое
        ]:
            response = self.client_web.patch(
                f'/map/api/positions/{position.pk}/', json.dumps(bad),
                content_type='application/json')
            self.assertEqual(response.status_code, 400, bad)

    def test_stale_version_conflict(self):
        """Второй администратор успел раньше — 409, изменения не теряются молча."""
        position = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=10, y=10, width=80, height=50)
        response = self.client_web.patch(
            f'/map/api/positions/{position.pk}/',
            json.dumps({'x': 50, 'y': 50, 'width': 80, 'height': 50,
                        'updated_at': '2000-01-01T00:00:00+00:00'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 409)
        position.refresh_from_db()
        self.assertEqual(position.x, 10)

    def test_swap(self):
        """Swap: №M-01 и №M-02 меняются позициями, бизнес-данные не тронуты."""
        position_a = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=10, y=10, width=80, height=50)
        position_b = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_b, x=500, y=300, width=90, height=60)
        response = self.client_web.post(
            '/map/api/positions/transfer',
            json.dumps({'source_id': position_a.pk, 'target_id': position_b.pk}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        position_a.refresh_from_db()
        position_b.refresh_from_db()
        self.assertEqual(position_a.spot, self.spot_b)
        self.assertEqual(position_b.spot, self.spot_a)
        # геометрия позиций не изменилась — поменялось только содержимое
        self.assertEqual(position_a.x, 10)
        self.assertEqual(position_b.x, 500)
        self.assertTrue(AuditLog.objects.filter(action='map_position_swap').exists())

    def test_move_to_empty_position(self):
        position_a = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=10, y=10, width=80, height=50)
        empty = MapPosition.objects.create(
            plan=self.plan, spot=None, x=600, y=400, width=80, height=50)
        response = self.client_web.post(
            '/map/api/positions/transfer',
            json.dumps({'source_id': position_a.pk, 'target_id': empty.pk}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        position_a.refresh_from_db()
        empty.refresh_from_db()
        self.assertIsNone(position_a.spot)
        self.assertEqual(empty.spot, self.spot_a)

    def test_transfer_from_empty_rejected(self):
        empty = MapPosition.objects.create(
            plan=self.plan, spot=None, x=10, y=10, width=80, height=50)
        target = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=200, y=10, width=80, height=50)
        response = self.client_web.post(
            '/map/api/positions/transfer',
            json.dumps({'source_id': empty.pk, 'target_id': target.pk}),
            content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_delete_keeps_spot(self):
        position = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=10, y=10, width=80, height=50)
        response = self.client_web.delete(f'/map/api/positions/{position.pk}/delete')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MapPosition.objects.count(), 0)
        self.assertTrue(Spot.objects.filter(pk=self.spot_a.pk).exists())

    def test_debt_flag_in_payload(self):
        """Место должника подсвечивается красным: has_debt из реального баланса."""
        import datetime
        from apps.billing.services import run_billing
        from django.utils import timezone
        tenant, tenant_spot = make_tenant_with_spot('9000.00')
        today = timezone.localdate()
        month = today.month % 12 + 1
        year = today.year + (1 if month == 1 else 0)
        run_billing(today=datetime.date(year, month, 1))
        MapPosition.objects.create(
            plan=self.plan, spot=tenant_spot.spot, x=10, y=10, width=80, height=50)
        data = self.client_web.get('/map/api/plan/').json()
        position = data['positions'][0]
        self.assertTrue(position['has_debt'])
        self.assertEqual(position['category'], 'yellow')  # оплачено не полностью
        self.assertEqual(position['tenant'], tenant.full_name)

    def test_section_create(self):
        """Раздел рынка = Building: создаётся по названию, код генерируется."""
        from apps.catalog.models import Building
        response = self.client_web.post(
            '/map/api/sections/', json.dumps({'name': 'Одежда'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Building.objects.filter(name='Одежда').exists())
        # дубль по названию отклоняется
        response = self.client_web.post(
            '/map/api/sections/', json.dumps({'name': 'одежда'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 409)

    def test_map_spot_create_requires_section(self):
        from apps.catalog.models import Building
        section = Building.objects.create(name='Обувь', code='ОБ')
        response = self.client_web.post(
            '/map/api/spots/', json.dumps({'code': '201', 'section_id': section.pk}),
            content_type='application/json')
        self.assertEqual(response.status_code, 201)
        spot = Spot.objects.get(code='201')
        self.assertEqual(spot.building, section)
        # без раздела — ошибка
        response = self.client_web.post(
            '/map/api/spots/', json.dumps({'code': '202'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 400)
        # дубль номера — конфликт
        response = self.client_web.post(
            '/map/api/spots/', json.dumps({'code': '201', 'section_id': section.pk}),
            content_type='application/json')
        self.assertEqual(response.status_code, 409)

    def test_zone_create_new_section(self):
        """«+ Раздел»: создаётся Building и его контур; только stroke, без заливки — фронт."""
        from apps.catalog.models import Building, MapZone
        response = self.client_web.post(
            '/map/api/zones/', json.dumps(
                {'name': 'Одежда', 'x': 100, 'y': 100, 'width': 500, 'height': 300}),
            content_type='application/json')
        self.assertEqual(response.status_code, 201)
        zone = MapZone.objects.get()
        self.assertEqual(zone.building.name, 'Одежда')
        self.assertEqual((zone.width, zone.height), (500, 300))
        # контур для существующего раздела без контура
        response = self.client_web.post(
            '/map/api/zones/', json.dumps(
                {'name': self.spot_a.building.name, 'x': 700, 'y': 100,
                 'width': 400, 'height': 200}),
            content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Building.objects.count(), 2)   # новый Building не создан
        # повторный контур того же раздела — конфликт
        response = self.client_web.post(
            '/map/api/zones/', json.dumps(
                {'name': 'Одежда', 'x': 0, 'y': 0, 'width': 300, 'height': 300}),
            content_type='application/json')
        self.assertEqual(response.status_code, 409)

    def test_zone_min_size_and_move(self):
        from apps.catalog.models import MapZone
        zone = MapZone.objects.create(
            plan=self.plan, building=self.spot_a.building,
            x=10, y=10, width=500, height=300)
        response = self.client_web.patch(
            f'/map/api/zones/{zone.pk}/',
            json.dumps({'x': 10, 'y': 10, 'width': 50, 'height': 300,
                        'updated_at': zone.updated_at.isoformat()}),
            content_type='application/json')
        self.assertEqual(response.status_code, 400)
        response = self.client_web.patch(
            f'/map/api/zones/{zone.pk}/',
            json.dumps({'x': 300, 'y': 200, 'width': 600, 'height': 400,
                        'updated_at': zone.updated_at.isoformat()}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        zone.refresh_from_db()
        self.assertEqual((zone.x, zone.y, zone.width, zone.height), (300, 200, 600, 400))

    def test_zone_rename_and_delete_removes_section_keeps_spots(self):
        """«Удалить раздел»: контур и Building удаляются, места остаются без раздела."""
        from apps.catalog.models import Building, MapZone
        building = self.spot_a.building
        zone = MapZone.objects.create(
            plan=self.plan, building=building, x=10, y=10, width=500, height=300)
        response = self.client_web.patch(
            f'/map/api/zones/{zone.pk}/',
            json.dumps({'name': 'Новое имя', 'updated_at': zone.updated_at.isoformat()}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        building.refresh_from_db()
        self.assertEqual(building.name, 'Новое имя')

        response = self.client_web.delete(f'/map/api/zones/{zone.pk}/delete')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['spots_detached'], 2)
        self.assertEqual(MapZone.objects.count(), 0)
        self.assertFalse(Building.objects.filter(pk=building.pk).exists())
        # места живы, но без раздела; арендаторы и позиции не тронуты
        self.spot_a.refresh_from_db()
        self.assertIsNone(self.spot_a.building)
        self.assertTrue(Spot.objects.filter(pk=self.spot_b.pk).exists())

    def test_zone_move_does_not_move_spots(self):
        """Перемещение контура не двигает MapPosition мест внутри него."""
        from apps.catalog.models import MapZone
        position = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=150, y=150, width=80, height=50)
        zone = MapZone.objects.create(
            plan=self.plan, building=self.spot_a.building,
            x=100, y=100, width=500, height=300)
        self.client_web.patch(
            f'/map/api/zones/{zone.pk}/',
            json.dumps({'x': 900, 'y': 600, 'width': 500, 'height': 300,
                        'updated_at': zone.updated_at.isoformat()}),
            content_type='application/json')
        position.refresh_from_db()
        self.assertEqual((position.x, position.y), (150, 150))

    def test_spot_set_section(self):
        """Перенос места в другой раздел: меняется только Spot.building."""
        from apps.catalog.models import Building
        other = Building.objects.create(name='Другой', code='ДР')
        position = MapPosition.objects.create(
            plan=self.plan, spot=self.spot_a, x=10, y=10, width=80, height=50)
        response = self.client_web.post(
            f'/map/api/spots/{self.spot_a.pk}/section',
            json.dumps({'section_id': other.pk}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.spot_a.refresh_from_db()
        self.assertEqual(self.spot_a.building, other)
        position.refresh_from_db()
        self.assertEqual((position.x, position.y), (10, 10))  # позиция не тронута
        # несуществующий раздел
        response = self.client_web.post(
            f'/map/api/spots/{self.spot_a.pk}/section',
            json.dumps({'section_id': 99999}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_map_categories(self):
        """Цвет категории: субаренда — синий, напрямую у рынка — фиолетовый,
        оплачено и сам ведёт — зелёный."""
        from apps.tenants.models import Tenant
        from apps.tenants.services import assign_spot
        from decimal import Decimal

        cases = [
            ({'rental_category': 'sublease'}, 'M-01', 'blue'),
            ({'rents_from_market': True}, 'M-02', 'purple'),
        ]
        for i, (attrs, code, expected) in enumerate(cases):
            tenant = Tenant.objects.create(
                full_name=f'K{i}', inn=f'7000000000000{i}')
            spot = Spot.objects.get(code=code)
            assign_spot(tenant=tenant, spot=spot,
                        monthly_amount=Decimal('1000'), **attrs)
            MapPosition.objects.create(
                plan=self.plan, spot=spot, x=100 * (i + 1), y=50, width=80, height=50)
        data = self.client_web.get('/map/api/plan/').json()
        categories = {p['code']: p['category'] for p in data['positions']}
        self.assertEqual(categories['M-01'], 'blue')
        self.assertEqual(categories['M-02'], 'purple')

    def test_requires_login(self):
        anonymous = Client()
        self.assertEqual(anonymous.get('/map/api/plan/').status_code, 302)
        response = anonymous.post(
            '/map/api/positions/', json.dumps({'spot_id': self.spot_a.pk,
                                               'x': 1, 'y': 1}),
            content_type='application/json')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MapPosition.objects.count(), 0)
