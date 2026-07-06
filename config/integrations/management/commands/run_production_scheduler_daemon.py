import time
from datetime import datetime, timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand

from config.integrations.quickbooks.alignment_sync import alignment_timezone


def _seconds_until_next_hour():
    now = datetime.now(alignment_timezone())
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max(60, int((next_hour - now).total_seconds()))


class Command(BaseCommand):
    help = (
        'Production daemon: wake every hour and run the QuickBooks alignment scheduler. '
        'Deploy this as a separate Railway worker service.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Run one scheduler check and exit (useful for Railway cron jobs).',
        )

    def handle(self, *args, **options):
        run_once = bool(options.get('once'))
        self.stdout.write(
            'QuickBooks production scheduler started. '
            'Alignment import runs at 12 AM, 6 AM, 12 PM, and 6 PM US Eastern.'
        )
        while True:
            try:
                call_command('run_scheduled_quickbooks_sync')
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'Scheduled QuickBooks sync failed: {exc}'))

            if run_once:
                return

            sleep_seconds = _seconds_until_next_hour()
            self.stdout.write(f'Next scheduler check in {sleep_seconds} seconds.')
            time.sleep(sleep_seconds)
