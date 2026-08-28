from django.contrib import admin

from .models import Building, MapPosition, MapZone, MarketPlan, Spot

admin.site.register(Building)
admin.site.register(Spot)
admin.site.register(MarketPlan)
admin.site.register(MapPosition)
admin.site.register(MapZone)
