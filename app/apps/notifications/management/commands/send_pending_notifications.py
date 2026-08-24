"""Отправка уведомлений из очереди: каждые 10 минут (ТЗ-02 раздел 8)."""
from django.core.management.base import BaseCommand

from apps.core.locks import command_lock
from apps.notifications.services import send_pending


class Command(BaseCommand):
    help = 'Отправка push-уведомлений из очереди, повтор неудачных не более 3 раз'

    def handle(self, *args, **options):
        with command_lock('send_pending_notifications'):
            sent, failed = send_pending()
        self.stdout.write(self.style.SUCCESS(f'Отправлено: {sent}, с ошибкой: {failed}'))
