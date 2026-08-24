"""Напоминания о сроке оплаты: ежедневно 09:00 (ТЗ-02 раздел 8, FR-NT-01)."""
import datetime

from django.core.management.base import BaseCommand

from apps.core.locks import command_lock
from apps.notifications.services import send_reminders


class Command(BaseCommand):
    help = 'Напоминания за 10, 5, 3 дня до срока и в день срока; уведомления о просрочке'

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, default=None,
                            help='Дата в формате YYYY-MM-DD (по умолчанию сегодня)')

    def handle(self, *args, **options):
        target = None
        if options['date']:
            target = datetime.date.fromisoformat(options['date'])
        with command_lock('send_reminders'):
            queued = send_reminders(today=target)
        self.stdout.write(self.style.SUCCESS(f'Поставлено в очередь напоминаний: {queued}'))
