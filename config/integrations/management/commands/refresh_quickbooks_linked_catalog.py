from django.core.management.base import BaseCommand, CommandError

from config.integrations.quickbooks.client import QuickBooksAPIError
from config.integrations.quickbooks.services import QuickBooksServiceError
from config.integrations.quickbooks.sync import QuickBooksSyncError, refresh_linked_quickbooks_items


class Command(BaseCommand):
    help = 'Refresh cost, prices, stock, and packaging for catalog rows already linked to QuickBooks.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0, help='Maximum linked items to refresh. Use 0 for all linked items.')

    def handle(self, *args, **options):
        raw_limit = int(options.get('limit') or 0)
        limit = None if raw_limit <= 0 else raw_limit
        try:
            result = refresh_linked_quickbooks_items(limit=limit)
        except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write('Linked QuickBooks catalog refresh complete.')
        self.stdout.write(
            f"Linked={result.get('linked_count', result.get('count', 0))} "
            f"updated={result.get('updated_count', 0)} "
            f"failed={result.get('failed_count', 0)}"
        )
