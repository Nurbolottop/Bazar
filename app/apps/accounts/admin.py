from django.contrib import admin

from .models import AuthToken, Device, TenantLoginLog

admin.site.register(Device)
admin.site.register(AuthToken)
admin.site.register(TenantLoginLog)
