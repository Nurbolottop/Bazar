from django.db import connection
from django.http import JsonResponse


def health(request):
    """Проверка работоспособности для внешнего мониторинга (ТЗ-02 п. 11.4)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok'})
    except Exception:
        return JsonResponse({'status': 'error'}, status=500)
