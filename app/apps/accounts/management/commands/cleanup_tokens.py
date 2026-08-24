"""Деактивация недействительных push-токенов: ежедневно 04:00 (ТЗ-02 раздел 8)."""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import Device
from apps.core.locks import command_lock

# Устройство считается неактивным, если не выходило на связь дольше этого срока
STALE_AFTER_DAYS = 180


class Command(BaseCommand):
    help = 'Деактивация push-токенов: отклонённые Firebase деактивируются при отправке, ' \
           'здесь чистятся давно не выходившие на связь устройства'

    def handle(self, *args, **options):
        with command_lock('cleanup_tokens'):
            threshold = timezone.now() - datetime.timedelta(days=STALE_AFTER_DAYS)
            stale = Device.objects.filter(is_active=True, last_seen_at__lt=threshold)
            count = stale.update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f'Деактивировано устройств: {count}'))
