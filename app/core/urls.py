from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from apps.core.views import health

urlpatterns = [
    # Встроенная админка — служебный инструмент разработчиков (ТЗ-02 п. 2.3),
    # в промышленном контуре закрывается на уровне nginx.
    path('django-admin/', admin.site.urls),
    path('health/', health, name='health'),
    path('api/v1/', include('apps.core.api.urls')),
    path('', include('apps.core.panel_urls')),
]

if settings.DEBUG:
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='api-docs'),
    ]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
