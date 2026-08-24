"""Инфраструктура веб-панели: доступ только для администраторов."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden


def admin_required(view):
    """Страницы панели доступны только сотрудникам администрации (is_staff)."""
    @wraps(view)
    @login_required(login_url='panel:login')
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden('Доступ запрещён')
        return view(request, *args, **kwargs)
    return wrapper


def paginate(request, queryset, per_page=50):
    """Все списки панели постраничные (ТЗ-02 п. 11.1)."""
    paginator = Paginator(queryset, per_page)
    page_number = request.GET.get('page') or 1
    return paginator.get_page(page_number)


def client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
