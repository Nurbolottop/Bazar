"""Панель: вход, главная, настройки, администраторы, журнал (ТЗ-02 п. 5.2)."""
import datetime

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.services import admin_login_blocked, record_admin_attempt
from apps.core.models import AuditLog, SystemSettings
from apps.core.money import ZERO
from apps.core.panel import admin_required, client_ip, paginate
from apps.core.services import audit
from apps.notifications.models import NotificationCode, NotificationTemplate
from apps.payments.models import Payment, PaymentClaim, TenantBalance


def login_view(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('panel:dashboard')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        if admin_login_blocked(username):
            error = 'Учётная запись временно заблокирована. Повторите через 15 минут.'
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None and user.is_staff and user.is_active:
                record_admin_attempt(username, True, client_ip(request))
                login(request, user)
                return redirect('panel:dashboard')
            record_admin_attempt(username, False, client_ip(request))
            error = 'Неверный логин или пароль.'
    return render(request, 'panel/login.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('panel:login')


@admin_required
def dashboard(request):
    """Главная (FR-RP-01): долг, должники, начислено и оплачено за месяц, заявки."""
    from apps.billing.models import Charge

    today = timezone.localdate()
    month_start = today.replace(day=1)

    total_debt = TenantBalance.objects.aggregate(s=Sum('debt_amount'))['s'] or ZERO
    debtors_count = TenantBalance.objects.filter(debt_amount__gt=0).count()
    charged_month = Charge.objects.filter(
        period_year=today.year, period_month=today.month,
    ).exclude(status=Charge.Status.CANCELLED).aggregate(s=Sum('amount'))['s'] or ZERO
    paid_month = Payment.objects.filter(
        status=Payment.Status.ACTIVE, paid_at__date__gte=month_start,
    ).aggregate(s=Sum('amount'))['s'] or ZERO
    recent_actions = AuditLog.objects.select_related('actor')[:15]

    return render(request, 'panel/dashboard.html', {
        'total_debt': total_debt,
        'debtors_count': debtors_count,
        'charged_month': charged_month,
        'paid_month': paid_month,
        'recent_actions': recent_actions,
    })


@admin_required
def claims_count(request):
    """JSON для автообновления счётчика в шапке (ТЗ-02 п. 5.3)."""
    count = PaymentClaim.objects.filter(status=PaymentClaim.Status.PENDING).count()
    return JsonResponse({'count': count})


@admin_required
def settings_view(request):
    """Настройки Системы (FR-AD-01)."""
    s = SystemSettings.load()
    if request.method == 'POST':
        old = {
            'default_billing_day': s.default_billing_day,
            'default_payment_day': s.default_payment_day,
        }
        s.market_name = request.POST.get('market_name', s.market_name)
        s.contacts = request.POST.get('contacts', s.contacts)
        s.payment_instruction_ru = request.POST.get('payment_instruction_ru', '')
        s.payment_instruction_ky = request.POST.get('payment_instruction_ky', '')
        s.min_app_version = request.POST.get('min_app_version', '')
        s.consent_version = request.POST.get('consent_version', s.consent_version)
        s.pin_login_enabled = request.POST.get('pin_login_enabled') == 'on'
        try:
            s.default_billing_day = int(request.POST.get('default_billing_day', s.default_billing_day))
            s.default_payment_day = int(request.POST.get('default_payment_day', s.default_payment_day))
            s.reminder_days = sorted({
                int(x) for x in request.POST.get('reminder_days', '').replace(' ', '').split(',') if x != ''
            }, reverse=True)
            s.overdue_notice_days = sorted({
                int(x) for x in request.POST.get('overdue_notice_days', '').replace(' ', '').split(',') if x != ''
            })
        except ValueError:
            messages.error(request, 'Дни указываются числами через запятую.')
            return redirect('panel:settings')
        s.reject_reasons = [
            line.strip() for line in request.POST.get('reject_reasons', '').splitlines()
            if line.strip()]
        if request.FILES.get('qr_image'):
            s.qr_image = request.FILES['qr_image']
        try:
            s.full_clean()
        except Exception as exc:
            messages.error(request, '; '.join(getattr(exc, 'messages', [str(exc)])))
            return redirect('panel:settings')
        s.save()
        audit(action='settings_update', model_name='SystemSettings', object_id=1,
              actor=request.user, old_value=old,
              new_value={'default_billing_day': s.default_billing_day,
                         'default_payment_day': s.default_payment_day},
              ip=client_ip(request))
        messages.success(request, 'Настройки сохранены.')
        return redirect('panel:settings')
    return render(request, 'panel/settings.html', {
        's': s,
        'reminder_days_str': ', '.join(str(d) for d in s.reminder_days),
        'overdue_days_str': ', '.join(str(d) for d in s.overdue_notice_days),
        'reject_reasons_str': '\n'.join(s.reject_reasons),
    })


@admin_required
def templates_view(request):
    """Шаблоны уведомлений (FR-NT-07): редактируемые тексты на двух языках."""
    from apps.notifications.services import DEFAULT_TEMPLATES

    if request.method == 'POST':
        code = request.POST.get('code')
        lang = request.POST.get('lang')
        title = request.POST.get('title_template', '').strip()
        body = request.POST.get('body_template', '').strip()
        if code in NotificationCode.values and lang in ('ru', 'ky') and title and body:
            NotificationTemplate.objects.update_or_create(
                code=code, lang=lang,
                defaults={'title_template': title, 'body_template': body})
            audit(action='template_update', model_name='NotificationTemplate',
                  object_id=f'{code}:{lang}', actor=request.user,
                  new_value={'title': title, 'body': body}, ip=client_ip(request))
            messages.success(request, 'Шаблон сохранён.')
        return redirect('panel:templates')

    saved = {(t.code, t.lang): t for t in NotificationTemplate.objects.all()}
    rows = []
    for code in NotificationCode.values:
        if code == NotificationCode.ANNOUNCEMENT:
            continue
        for lang in ('ru', 'ky'):
            template = saved.get((code, lang))
            default_title, default_body = DEFAULT_TEMPLATES.get(code, {}).get(
                lang, DEFAULT_TEMPLATES.get(code, {}).get('ru', ('', '')))
            rows.append({
                'code': code,
                'label': NotificationCode(code).label,
                'lang': lang,
                'title': template.title_template if template else default_title,
                'body': template.body_template if template else default_body,
                'is_default': template is None,
            })
    return render(request, 'panel/templates.html', {'rows': rows})


@admin_required
def admins_view(request):
    """Учётные записи администраторов: создание, блокировка, смена пароля (FR-AD-02)."""
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '')
            if not username or User.objects.filter(username=username).exists():
                messages.error(request, 'Логин пуст или уже занят.')
            else:
                try:
                    from django.contrib.auth.password_validation import validate_password
                    validate_password(password)
                    user = User.objects.create_user(
                        username=username, password=password, is_staff=True)
                    audit(action='admin_create', model_name='User', object_id=user.pk,
                          actor=request.user, new_value={'username': username},
                          ip=client_ip(request))
                    messages.success(request, f'Администратор {username} создан.')
                except Exception as exc:
                    messages.error(request, '; '.join(getattr(exc, 'messages', [str(exc)])))
        elif action in ('block', 'unblock'):
            target = User.objects.filter(pk=request.POST.get('user_id'), is_staff=True).first()
            if target and target.pk != request.user.pk:
                target.is_active = action == 'unblock'
                target.save(update_fields=['is_active'])
                audit(action=f'admin_{action}', model_name='User', object_id=target.pk,
                      actor=request.user, ip=client_ip(request))
                messages.success(request, 'Готово.')
            else:
                messages.error(request, 'Нельзя заблокировать собственную учётную запись.')
        elif action == 'set_password':
            target = User.objects.filter(pk=request.POST.get('user_id'), is_staff=True).first()
            if target:
                try:
                    from django.contrib.auth.password_validation import validate_password
                    validate_password(request.POST.get('password', ''), user=target)
                    target.set_password(request.POST.get('password', ''))
                    target.save()
                    audit(action='admin_password_change', model_name='User',
                          object_id=target.pk, actor=request.user, ip=client_ip(request))
                    messages.success(request, 'Пароль изменён.')
                except Exception as exc:
                    messages.error(request, '; '.join(getattr(exc, 'messages', [str(exc)])))
        return redirect('panel:admins')

    admins = User.objects.filter(is_staff=True).order_by('username')
    return render(request, 'panel/admins.html', {'admins': admins})


@admin_required
def audit_log_view(request):
    """Журнал действий: фильтры, только просмотр (FR-AD-03, FR-AD-04)."""
    qs = AuditLog.objects.select_related('actor')
    actor_id = request.GET.get('actor')
    if actor_id and actor_id.isdigit():
        qs = qs.filter(actor_id=int(actor_id))
    action = request.GET.get('action', '').strip()
    if action:
        qs = qs.filter(action=action)
    model_name = request.GET.get('model', '').strip()
    if model_name:
        qs = qs.filter(model_name=model_name)
    date_from = request.GET.get('from')
    if date_from:
        try:
            qs = qs.filter(created_at__date__gte=datetime.date.fromisoformat(date_from))
        except ValueError:
            pass
    date_to = request.GET.get('to')
    if date_to:
        try:
            qs = qs.filter(created_at__date__lte=datetime.date.fromisoformat(date_to))
        except ValueError:
            pass

    page = paginate(request, qs, per_page=100)
    actions = AuditLog.objects.values_list('action', flat=True).distinct().order_by('action')
    admins = User.objects.filter(is_staff=True).order_by('username')
    return render(request, 'panel/audit_log.html', {
        'page': page, 'actions': actions, 'admins': admins,
    })
