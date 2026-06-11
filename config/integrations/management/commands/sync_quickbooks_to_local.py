from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config.integrations.quickbooks.client import QuickBooksAPIError
from config.integrations.quickbooks.services import QuickBooksServiceError
from config.integrations.quickbooks.sync import QuickBooksSyncError, pull_quickbooks_accounting_documents_to_local, pull_quickbooks_items_to_local, pull_quickbooks_to_local


class Command(BaseCommand):
    help = 'Pull customers, catalog, and accounting document matches from QuickBooks into the local app review queue.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0, help='Maximum records per QuickBooks entity to pull during this run. Use 0 for no cap.')
        parser.add_argument('--full', action='store_true', help='Ignore saved cursors and run a full pull.')
        parser.add_argument('--items-only', action='store_true', help='Refresh only QuickBooks catalog items and their linked local products.')
        parser.add_argument('--accounting-only', action='store_true', help='Pull only QuickBooks invoices and credit memos into local accounting records.')

    def handle(self, *args, **options):
        items_only = bool(options.get('items_only'))
        accounting_only = bool(options.get('accounting_only'))
        if items_only and accounting_only:
            raise CommandError('Use only one of --items-only or --accounting-only.')
        if getattr(settings, 'QUICKBOOKS_CATALOG_ONLY_MODE', True) and not items_only and not accounting_only:
            raise CommandError(
                'Full QuickBooks pull sync is disabled while QUICKBOOKS_CATALOG_ONLY_MODE is enabled. '
                'Use --items-only, --accounting-only, or set QUICKBOOKS_CATALOG_ONLY_MODE=False.'
            )
        raw_limit = int(options.get('limit') or 0)
        limit = None if raw_limit <= 0 else raw_limit
        try:
            if items_only:
                result = pull_quickbooks_items_to_local(max_results=limit, force_full=bool(options.get('full')))
            elif accounting_only:
                result = pull_quickbooks_accounting_documents_to_local(max_results=limit, force_full=bool(options.get('full')))
            else:
                result = pull_quickbooks_to_local(max_results=limit, force_full=bool(options.get('full')))
        except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
            raise CommandError(str(exc)) from exc

        items = result.get('items', {})
        if items_only:
            self.stdout.write('QuickBooks catalog sync complete.')
            self.stdout.write(
                f"Catalog: created={items.get('created_count', 0)} updated={items.get('updated_count', 0)} conflicts={items.get('conflict_count', 0)}"
            )
            self.stdout.write(f"Mode: {'full' if options.get('full') else 'incremental'}")
            return

        if accounting_only:
            self.stdout.write('QuickBooks accounting pull complete.')
            self.stdout.write(
                f"Accounting docs: created={result.get('created_count', 0)} updated={result.get('updated_count', 0)} conflicts={result.get('conflict_count', 0)}"
            )
            self.stdout.write(f"Mode: {'full' if options.get('full') else 'incremental'}")
            return

        customers = result.get('customers', {})
        accounting = result.get('accounting_documents', {})
        self.stdout.write('QuickBooks pull sync complete.')
        self.stdout.write(
            f"Customers: created={customers.get('created_count', 0)} updated={customers.get('updated_count', 0)} conflicts={customers.get('conflict_count', 0)}"
        )
        self.stdout.write(
            f"Catalog: created={items.get('created_count', 0)} updated={items.get('updated_count', 0)} conflicts={items.get('conflict_count', 0)}"
        )
        self.stdout.write(
            f"Accounting docs: matched={accounting.get('matched_count', 0)} conflicts={accounting.get('conflict_count', 0)}"
        )
        self.stdout.write(f"Mode: {'full' if options.get('full') else 'incremental'}")