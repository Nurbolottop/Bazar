from django.contrib import admin

from .models import AuditLog, SystemSettings

admin.site.register(SystemSettings)
admin.site.register(AuditLog)
