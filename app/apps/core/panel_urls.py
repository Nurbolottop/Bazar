"""Маршруты веб-панели администрации (ТЗ-02 раздел 5)."""
from django.urls import path

from apps.billing import panel_views as billing
from apps.catalog import map_views, panel_views as catalog
from apps.core import panel_views as core
from apps.notifications import panel_views as notifications
from apps.payments import panel_views as payments
from apps.reports import panel_views as reports
from apps.tenants import import_views, panel_views as tenants

app_name = 'panel'

urlpatterns = [
    path('login/', core.login_view, name='login'),
    path('logout/', core.logout_view, name='logout'),
    path('', core.dashboard, name='dashboard'),
    path('claims/count/', core.claims_count, name='claims_count'),

    # Заявки об оплате
    path('claims/', payments.claims_list, name='claims'),
    path('claims/<int:pk>/', payments.claim_detail, name='claim_detail'),
    path('claims/<int:pk>/confirm/', payments.claim_confirm, name='claim_confirm'),
    path('claims/<int:pk>/reject/', payments.claim_reject, name='claim_reject'),

    # Платежи
    path('payments/', payments.payments_list, name='payments'),
    path('payments/<int:pk>/reverse/', payments.payment_reverse, name='payment_reverse'),

    # Арендаторы
    path('tenants/', tenants.tenants_list, name='tenants'),
    path('tenants/new/', tenants.tenant_form, name='tenant_new'),
    path('tenants/<int:pk>/', tenants.tenant_detail, name='tenant_detail'),
    path('tenants/<int:pk>/edit/', tenants.tenant_form, name='tenant_edit'),
    path('tenants/<int:pk>/assign-spot/', tenants.tenant_assign_spot, name='tenant_assign_spot'),
    path('tenants/<int:pk>/release-spot/<int:tenant_spot_id>/',
         tenants.tenant_release_spot, name='tenant_release_spot'),
    path('tenants/<int:pk>/status/', tenants.tenant_set_status, name='tenant_set_status'),
    path('tenants/<int:pk>/revoke-tokens/', tenants.tenant_revoke_tokens,
         name='tenant_revoke_tokens'),
    path('tenants/<int:pk>/documents/', tenants.tenant_upload_document,
         name='tenant_upload_document'),
    path('tenants/<int:tenant_id>/payment/', payments.tenant_manual_payment,
         name='tenant_manual_payment'),
    path('tenants/<int:tenant_id>/adjust/', payments.tenant_adjust_debt,
         name='tenant_adjust_debt'),
    path('tenants/<int:tenant_id>/charge/', billing.charge_create_manual,
         name='tenant_manual_charge'),

    # Массовое редактирование сумм
    path('amounts/', tenants.mass_amounts, name='mass_amounts'),

    # Начисления
    path('charges/', billing.charges_list, name='charges'),
    path('charges/preview/', billing.billing_preview, name='billing_preview'),
    path('charges/run/', billing.billing_run, name='billing_run'),
    path('charges/<int:pk>/cancel/', billing.charge_cancel, name='charge_cancel'),

    # Места: интерактивная карта рынка
    path('spots/', map_views.map_page, name='spots'),
    path('map/api/plan/', map_views.map_plan_json, name='map_plan'),
    path('map/api/positions/', map_views.position_create, name='map_position_create'),
    path('map/api/positions/<int:pk>/', map_views.position_update, name='map_position_update'),
    path('map/api/positions/<int:pk>/delete', map_views.position_delete, name='map_position_delete'),
    path('map/api/positions/transfer', map_views.position_transfer, name='map_position_transfer'),
    path('map/api/sections/', map_views.section_create, name='map_section_create'),
    path('map/api/zones/', map_views.zone_create, name='map_zone_create'),
    path('map/api/zones/<int:pk>/', map_views.zone_update, name='map_zone_update'),
    path('map/api/zones/<int:pk>/delete', map_views.zone_delete, name='map_zone_delete'),
    path('map/api/spots/', map_views.map_spot_create, name='map_spot_create'),
    path('spots/list/', catalog.spots_table, name='spots_table'),
    path('spots/manage/', catalog.spots_manage, name='spots_manage'),
    path('spots/create/', catalog.spot_create, name='spot_create'),
    path('spots/mass-create/', catalog.spots_mass_create, name='spots_mass_create'),
    path('spots/<int:pk>/history/', catalog.spot_history, name='spot_history'),
    path('buildings/create/', catalog.building_create, name='building_create'),

    # Должники, отчёты
    path('debtors/', reports.debtors_view, name='debtors'),
    path('reports/', reports.reports_view, name='reports'),

    # Импорт
    path('import/', import_views.import_view, name='import'),
    path('import/template/', import_views.import_template, name='import_template'),

    # Объявления
    path('announcements/', notifications.announcements_view, name='announcements'),

    # Настройки, администраторы, журнал
    path('settings/', core.settings_view, name='settings'),
    path('settings/templates/', core.templates_view, name='templates'),
    path('admins/', core.admins_view, name='admins'),
    path('audit/', core.audit_log_view, name='audit'),
]
