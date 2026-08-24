"""Контекст панели: счётчик необработанных заявок виден на всех страницах (FR-PM-12)."""


def panel_context(request):
    if not getattr(request.user, 'is_staff', False):
        return {}
    from apps.payments.models import PaymentClaim
    return {
        'pending_claims_count': PaymentClaim.objects.filter(
            status=PaymentClaim.Status.PENDING).count(),
    }
