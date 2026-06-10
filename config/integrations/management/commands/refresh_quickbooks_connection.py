from django.core.management.base import BaseCommand, CommandError

from config.integrations.quickbooks.services import (
    QuickBooksServiceError,
    get_connection_status,
    maintain_quickbooks_connection,
)


class Command(BaseCommand):
    help = 'Refresh QuickBooks OAuth tokens so the production connection stays active without manual reconnects.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Refresh even if the current access token is still valid.',
        )

    def handle(self, *args, **options):
        status_before = get_connection_status()
        if not status_before.get('is_active'):
            self.stdout.write(self.style.WARNING('QuickBooks is not connected. Nothing to refresh.'))
            return

        try:
            result = maintain_quickbooks_connection(force=bool(options.get('force')))
        except QuickBooksServiceError as exc:
            raise CommandError(str(exc)) from exc

        if result.get('refreshed'):
            self.stdout.write(self.style.SUCCESS('QuickBooks tokens refreshed successfully.'))
        else:
            self.stdout.write(f"QuickBooks connection kept as-is ({result.get('reason', 'unknown')}).")

        status_after = get_connection_status()
        self.stdout.write(f"Active: {status_after.get('is_active')}")
        self.stdout.write(f"Last refreshed: {status_after.get('last_refreshed_at') or 'never'}")
        if status_after.get('last_error'):
            self.stdout.write(self.style.WARNING(f"Last error: {status_after['last_error']}"))
