"""Панель: импорт данных из Excel (страница «Импорт данных», ТЗ-02 п. 5.2)."""
from django.contrib import messages
from django.shortcuts import redirect, render

from apps.core.panel import admin_required

from . import import_service


@admin_required
def import_view(request):
    report = None
    mode = None
    if request.method == 'POST':
        file = request.FILES.get('file')
        mode = request.POST.get('mode', 'dry')
        if not file:
            messages.error(request, 'Выберите файл XLSX.')
            return redirect('panel:import')
        report = import_service.run_import(
            file, dry_run=(mode != 'final'), actor=request.user)
        if mode == 'final' and report.ok:
            messages.success(
                request,
                f'Импорт выполнен: арендаторов создано {report.created_tenants}, '
                f'обновлено {report.updated_tenants}, мест создано {report.created_spots}, '
                f'привязок {report.created_links}, начальный долг '
                f'{report.total_initial_debt} сом.')
        elif not report.ok:
            messages.error(
                request,
                f'Найдено ошибок: {len(report.errors)}. '
                + ('Данные не записаны.' if mode == 'final' else 'Исправьте файл и повторите.'))
    return render(request, 'panel/import.html', {'report': report, 'mode': mode})


@admin_required
def import_template(request):
    return import_service.template_workbook_response()
