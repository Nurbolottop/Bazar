from django.contrib import admin

from .models import BillingRun, Charge

admin.site.register(Charge)
admin.site.register(BillingRun)
