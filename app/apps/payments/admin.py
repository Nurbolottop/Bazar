from django.contrib import admin

from .models import DebtAdjustment, Payment, PaymentAllocation, PaymentClaim, TenantBalance

admin.site.register(PaymentClaim)
admin.site.register(Payment)
admin.site.register(PaymentAllocation)
admin.site.register(DebtAdjustment)
admin.site.register(TenantBalance)
