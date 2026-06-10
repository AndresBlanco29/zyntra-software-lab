from django.core.management.base import BaseCommand, CommandError

from config.integrations.quickbooks.services import QuickBooksServiceError, quickbooks_credentials_configured
from config.integrations.quickbooks.sync import sync_missing_quickbooks_item_images


class Command(BaseCommand):
    help = 'Download missing product images from QuickBooks attachments for imported catalog items.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None, help='Maximum number of products to check.')
        parser.add_argument('--dry-run', action='store_true', help='Report how many images are available without saving files.')

    def handle(self, *args, **options):
        if not quickbooks_credentials_configured():
            raise CommandError('QuickBooks credentials are not configured.')

        try:
            summary = sync_missing_quickbooks_item_images(
                limit=options.get('limit'),
                dry_run=bool(options.get('dry_run')),
            )
        except QuickBooksServiceError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                'QuickBooks image sync complete: '
                f"checked={summary['checked']} synced={summary['synced']} "
                f"missing_in_qb={summary['missing_in_qb']} failed={summary['failed']}"
            )
        )
        for label in summary.get('synced_labels', [])[:25]:
            self.stdout.write(f'  - {label}')
        if len(summary.get('synced_labels', [])) > 25:
            self.stdout.write(f'  ... and {len(summary["synced_labels"]) - 25} more')
