from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from config.integrations.models import QuickBooksImportConflict
from config.inventario.models import CompraProveedorLinea, InventarioMovimiento
from config.productos.models import Presentacion, Producto


class Command(BaseCommand):
    help = 'Delete all products and presentations so QuickBooks catalog import can start from a clean slate.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            required=True,
            help='Type CLEAR_CATALOG to confirm deletion.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show counts only; do not delete anything.',
        )

    def handle(self, *args, **options):
        if str(options.get('confirm') or '').strip().upper() != 'CLEAR_CATALOG':
            raise CommandError('Cancelled. Re-run with --confirm=CLEAR_CATALOG')

        counts = {
            'compra_lineas': CompraProveedorLinea.objects.count(),
            'movimientos': InventarioMovimiento.objects.count(),
            'qb_item_conflicts': QuickBooksImportConflict.objects.filter(
                entity_type=QuickBooksImportConflict.ENTITY_ITEM,
            ).count(),
            'presentaciones': Presentacion.objects.count(),
            'productos': Producto.objects.count(),
        }
        self.stdout.write('Catalog rows to remove:')
        for label, value in counts.items():
            self.stdout.write(f'  - {label}: {value}')

        if options.get('dry_run'):
            self.stdout.write(self.style.WARNING('Dry run only. No data was deleted.'))
            return

        with transaction.atomic():
            CompraProveedorLinea.objects.all().delete()
            InventarioMovimiento.objects.all().delete()
            QuickBooksImportConflict.objects.filter(
                entity_type=QuickBooksImportConflict.ENTITY_ITEM,
            ).delete()
            deleted_presentaciones, _ = Presentacion.objects.all().delete()
            deleted_productos, _ = Producto.objects.all().delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'Catalog cleared: {deleted_productos} products, {deleted_presentaciones} presentations.'
            )
        )
        self.stdout.write('You can now import the catalog from QuickBooks (catalog-only mode).')
