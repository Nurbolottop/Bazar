"""Перевод начислений в статус overdue: ежедневно 00:50 (ТЗ-02 раздел 8)."""
from django.core.management.base import BaseCommand

from apps.billing.services import update_overdue_statuses
from apps.core.locks import command_lock


class Command(BaseCommand):
    help = 'Перевод в статус overdue начислений, у которых истёк due_date'

    def handle(self, *args, **options):
        with command_lock('update_charge_statuses'):
            updated = update_overdue_statuses()
        self.stdout.write(self.style.SUCCESS(f'Просрочено начислений: {updated}'))
