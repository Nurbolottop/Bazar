"""Прогон начислений: ежедневно 00:30 по Asia/Bishkek (ТЗ-02 раздел 8)."""
import datetime

from django.core.management.base import BaseCommand

from apps.billing.services import run_billing
from apps.core.locks import command_lock


class Command(BaseCommand):
    help = 'Создание начислений у арендаторов, у которых наступил billing_day'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Предварительный просмотр: расчёт без записи в базу (FR-CH-07)')
        parser.add_argument(
            '--date', type=str, default=None,
            help='Дата прогона в формате YYYY-MM-DD (по умолчанию сегодня)')

    def handle(self, *args, **options):
        run_date = None
        if options['date']:
            run_date = datetime.date.fromisoformat(options['date'])

        with command_lock('run_billing'):
            run = run_billing(today=run_date, dry_run=options['dry_run'])

        kind = 'Просмотр' if run.dry_run else 'Прогон'
        self.stdout.write(self.style.SUCCESS(
            f'{kind} {run.run_date}: создано {run.created_count} '
            f'на сумму {run.total_amount}, пропущено {len(run.skipped)}, '
            f'ошибок {len(run.errors)}'))
        for error in run.errors:
            self.stderr.write(str(error))
