"""Общие сервисы: журнал действий, настройки."""
from typing import Any

from .models import AuditLog, SystemSettings


def get_settings() -> SystemSettings:
    return SystemSettings.load()


def audit(
    *,
    action: str,
    model_name: str,
    object_id: Any,
    actor=None,
    actor_type: str = AuditLog.ActorType.ADMIN,
    old_value=None,
    new_value=None,
    ip: str | None = None,
    user_agent: str = '',
) -> AuditLog:
    """Запись в журнал действий (FR-AD-03)."""
    return AuditLog.objects.create(
        actor=actor if getattr(actor, 'pk', None) else None,
        actor_type=actor_type,
        action=action,
        model_name=model_name,
        object_id=str(object_id),
        old_value=old_value,
        new_value=new_value,
        ip=ip,
        user_agent=user_agent[:512] if user_agent else '',
    )
