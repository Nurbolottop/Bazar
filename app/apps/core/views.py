import os

from django.conf import settings
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse


def health(request):
    """Проверка работоспособности для внешнего мониторинга (ТЗ-02 п. 11.4)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok'})
    except Exception:
        return JsonResponse({'status': 'error'}, status=500)


def protected_media(request, path: str):
    """Выдача файлов /media/ с проверкой прав (ТЗ-02 п. 7.3, 9.1).

    Чеки, фото и документы не отдаются веб-сервером напрямую: доступ только
    администраторам по сессии. Публичен только каталог qr/ (QR-код рынка).
    Мобильное приложение получает чеки через /api/v1/me/receipts/.
    """
    normalized = os.path.normpath(path).replace('\\', '/')
    if normalized.startswith(('..', '/')):
        raise Http404
    if not normalized.startswith('qr/') and not request.user.is_staff:
        raise Http404
    full_path = os.path.join(settings.MEDIA_ROOT, normalized)
    if not os.path.isfile(full_path):
        raise Http404
    return FileResponse(open(full_path, 'rb'))
