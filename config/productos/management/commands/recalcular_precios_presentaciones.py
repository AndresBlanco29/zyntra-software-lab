from django.core.management.base import BaseCommand

from config.productos.models import Presentacion


class Command(BaseCommand):
    help = (
        'Recalculate and persist Price 1-5 for every presentation that has a cost. '
        'Use after QuickBooks cost updates that previously left stale price tiers in Orders.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show how many presentations would be recalculated without saving.',
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get('dry_run'))
        queryset = Presentacion.objects.exclude(costo__isnull=True).order_by('id')
        total = queryset.count()
        updated = 0

        for presentacion in queryset.iterator(chunk_size=200):
            before = (
                presentacion.precio_1,
                presentacion.precio_2,
                presentacion.precio_3,
                presentacion.precio_4,
                presentacion.precio_5,
            )
            presentacion.recalcular_precios()
            after = (
                presentacion.precio_1,
                presentacion.precio_2,
                presentacion.precio_3,
                presentacion.precio_4,
                presentacion.precio_5,
            )
            if before == after:
                continue
            updated += 1
            if not dry_run:
                presentacion.save(update_fields=['precio_1', 'precio_2', 'precio_3', 'precio_4', 'precio_5'])

        action = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'{action} {updated} of {total} presentations with a cost.'
        ))
