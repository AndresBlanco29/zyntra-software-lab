from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config.core.demo_showcase import seed_demo_showcase


class Command(BaseCommand):
    help = (
        'Seed fictitious Software Lab showcase data. '
        'Requires DEMO_MODE=1. Never run against La Tortilla Grocery production.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing showcase/business rows before seeding (DEMO only).',
        )

    def handle(self, *args, **options):
        if not getattr(settings, 'DEMO_MODE', False):
            raise CommandError(
                'Refusing to seed: DEMO_MODE is off. '
                'Enable DEMO_MODE=1 on an isolated database first.'
            )
        if getattr(settings, 'QUICKBOOKS_ENVIRONMENT', '').lower() == 'production':
            raise CommandError('Refusing to seed while QUICKBOOKS_ENVIRONMENT=production.')

        try:
            summary = seed_demo_showcase(reset=options['reset'])
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS('Demo showcase seed completed.'))
        self.stdout.write(f"  Login: {summary['demo_login']}")
        self.stdout.write(f"  Password: {summary['demo_password']}")
        self.stdout.write(f"  Customers: {summary['customers']}")
        self.stdout.write(f"  Presentations: {summary['presentations']}")
        self.stdout.write(f"  Quotes: {summary['quotes']}")
        self.stdout.write(f"  Orders: {summary['orders']}")
        self.stdout.write(f"  Invoices: {summary['invoices']}")
        self.stdout.write(f"  Deliveries: {summary['deliveries']}")
        self.stdout.write(f"  QB sync runs: {summary['qb_sync_runs']}")
