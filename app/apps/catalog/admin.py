from django.contrib import admin

from .models import Building, MapPosition, MarketPlan, Spot

admin.site.register(Building)
admin.site.register(Spot)
admin.site.register(MarketPlan)
admin.site.register(MapPosition)
