from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from config.cotizaciones.models import CotizacionItem
from config.integrations.models import QuickBooksConnection, QuickBooksImportConflict
from config.inventario.models import (
    CompraProveedorLinea,
    InventarioMovimiento,
    StockPresentacion,
    StockProductoFraccionado,
)
from config.pedidos.models import PedidoItem
from config.productos.models import Presentacion, Producto


class Command(BaseCommand):
    help = (
        'Delete all products and presentations so QuickBooks catalog import can start from a clean slate. '
        'Order and quote line items linked to presentations are removed as well.'
    )

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
            'pedido_items': PedidoItem.objects.count(),
            'cotizacion_items': CotizacionItem.objects.count(),
            'compra_lineas': CompraProveedorLinea.objects.count(),
            'movimientos': InventarioMovimiento.objects.count(),
            'stock_presentacion': StockPresentacion.objects.count(),
            'stock_fraccionado': StockProductoFraccionado.objects.count(),
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
            StockPresentacion.objects.all().delete()
            StockProductoFraccionado.objects.all().delete()
            deleted_presentaciones, _ = Presentacion.objects.all().delete()
            deleted_productos, _ = Producto.objects.all().delete()

        connection = QuickBooksConnection.get_solo()
        connection.clear_sync_cursor('quickbooks:item')
        connection.save(update_fields=['sync_state', 'updated_at'])
        cache.delete('catalogo:productos_activos_v2')

        self.stdout.write(
            self.style.SUCCESS(
                f'Catalog cleared: {deleted_productos} products, {deleted_presentaciones} presentations.'
            )
        )
        self.stdout.write('QuickBooks item sync cursor reset.')
        self.stdout.write('You can now import the catalog from QuickBooks.')
