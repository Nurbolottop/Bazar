"""Панель: объявления администрации (FR-NT-06)."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.catalog.models import Building
from apps.core.panel import admin_required, client_ip, paginate
from apps.core.services import audit

from . import services
from .models import Announcement


@admin_required
def announcements_view(request):
    if request.method == 'POST':
        title_ru = request.POST.get('title_ru', '').strip()
        body_ru = request.POST.get('body_ru', '').strip()
        if not title_ru or not body_ru:
            messages.error(request, 'Заполните заголовок и текст на русском языке.')
            return redirect('panel:announcements')
        audience = request.POST.get('audience', Announcement.Audience.ALL)
        if audience not in dict(Announcement.Audience.choices):
            audience = Announcement.Audience.ALL
        building = None
        if audience == Announcement.Audience.BUILDING:
            building = Building.objects.filter(pk=request.POST.get('building_id')).first()
            if building is None:
                messages.error(request, 'Выберите корпус.')
                return redirect('panel:announcements')
        announcement = Announcement.objects.create(
            title_ru=title_ru, body_ru=body_ru,
            title_ky=request.POST.get('title_ky', '').strip(),
            body_ky=request.POST.get('body_ky', '').strip(),
            audience=audience, building=building, created_by=request.user)
        count = services.publish_announcement(announcement)
        audit(action='announcement_send', model_name='Announcement',
              object_id=announcement.pk, actor=request.user,
              new_value={'audience': audience, 'recipients': count},
              ip=client_ip(request))
        messages.success(request, f'Объявление отправлено {count} арендаторам.')
        return redirect('panel:announcements')

    page = paginate(request, Announcement.objects.select_related('building', 'created_by'))
    return render(request, 'panel/announcements.html', {
        'page': page,
        'audiences': Announcement.Audience.choices,
        'buildings': Building.objects.filter(is_active=True),
    })
