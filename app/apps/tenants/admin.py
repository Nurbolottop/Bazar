from django.contrib import admin

from .models import Tenant, TenantDocument, TenantSpot

admin.site.register(Tenant)
admin.site.register(TenantSpot)
admin.site.register(TenantDocument)
