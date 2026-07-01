from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.utils import DatabaseError

from config.clientes.models import Cliente, ClienteCreditoLimiteAlerta
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.facturacion.models import (
    Delivery,
    DeliveryEvidencePhoto,
    DeliveryNotificationLog,
    DeliveryPayment,
    FacturacionRegistroAnulacion,
    Invoice,
    InvoiceItem,
    NotaAjuste,
    NotaAjusteAplicacion,
    NotaAjusteEvidencePhoto,
    NotaAjusteItem,
)
from config.integrations.models import QuickBooksConnection, QuickBooksImportConflict
from config.inventario.models import (
    CompraProveedor,
    CompraProveedorLinea,
    InventarioMovimiento,
    Proveedor,
    StockPresentacion,
    StockProductoFraccionado,
)
from config.notificaciones.models import Notificacion
from config.pedidos.models import Pedido, PedidoEditLock, PedidoItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


QB_CURSOR_KEYS = (
    'quickbooks:customer',
    'quickbooks:item',
    'quickbooks:invoice',
    'quickbooks:credit_memo',
)


def _safe_count(queryset):
    try:
        return queryset.count()
    except DatabaseError:
        return 0


def _raw_delete_table(model):
    table_name = model._meta.db_table
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'DELETE FROM `{table_name}`')
            return cursor.rowcount
    except DatabaseError:
        return 0


def _safe_delete(queryset, *, allow_raw_delete=True):
    try:
        return queryset.delete()
    except DatabaseError:
        model_label = queryset.model._meta.label
        if not allow_raw_delete or model_label in {'usuarios.Usuario', 'auth.User'}:
            return 0, {}
        deleted_count = _raw_delete_table(queryset.model)
        return deleted_count, {model_label: deleted_count}


class Command(BaseCommand):
    help = (
        'Delete customers, products, orders, invoices, inventory movements, and QuickBooks review rows '
        'so catalog and customer imports can be tested from scratch. Internal users are preserved.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            required=True,
            help='Type RESET_TEST_ENV to confirm deletion.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show counts only; do not delete anything.',
        )

    def handle(self, *args, **options):
        if str(options.get('confirm') or '').strip().upper() != 'RESET_TEST_ENV':
            raise CommandError('Cancelled. Re-run with --confirm=RESET_TEST_ENV')

        counts = self._build_counts()
        self.stdout.write('Rows to remove:')
        for label, value in counts.items():
            self.stdout.write(f'  - {label}: {value}')
        self.stdout.write(f'  - internal_users_kept: {Usuario.objects.exclude(role="cliente").count()}')

        if options.get('dry_run'):
            self.stdout.write(self.style.WARNING('Dry run only. No data was deleted.'))
            return

        self._delete_all()

        internal_users_kept = Usuario.objects.exclude(role='cliente').count()
        self.stdout.write(self.style.SUCCESS('Test environment reset complete.'))
        self.stdout.write(f'Internal users kept: {internal_users_kept}')
        if internal_users_kept == 0:
            self.stdout.write(
                self.style.WARNING(
                    'No internal users remain. Run `python manage.py ensure_superuser` after setting '
                    'DJANGO_SUPERUSER_USERNAME and DJANGO_SUPERUSER_PASSWORD.'
                )
            )
        self.stdout.write('You can now import customers and products from QuickBooks.')

    def _build_counts(self):
        return {
            'nota_aplicaciones': _safe_count(NotaAjusteAplicacion.objects.all()),
            'nota_items': _safe_count(NotaAjusteItem.objects.all()),
            'nota_evidence': _safe_count(NotaAjusteEvidencePhoto.objects.all()),
            'notas_ajuste': _safe_count(NotaAjuste.objects.all()),
            'facturacion_anulaciones': _safe_count(FacturacionRegistroAnulacion.objects.all()),
            'delivery_payments': _safe_count(DeliveryPayment.objects.all()),
            'delivery_evidence': _safe_count(DeliveryEvidencePhoto.objects.all()),
            'delivery_notifications': _safe_count(DeliveryNotificationLog.objects.all()),
            'deliveries': _safe_count(Delivery.objects.all()),
            'invoice_items': _safe_count(InvoiceItem.objects.all()),
            'invoices': _safe_count(Invoice.objects.all()),
            'pedido_edit_locks': _safe_count(PedidoEditLock.objects.all()),
            'pedido_items': _safe_count(PedidoItem.objects.all()),
            'pedidos': _safe_count(Pedido.objects.all()),
            'cotizacion_items': _safe_count(CotizacionItem.objects.all()),
            'cotizaciones': _safe_count(Cotizacion.objects.all()),
            'cliente_credito_alertas': _safe_count(ClienteCreditoLimiteAlerta.objects.all()),
            'clientes': _safe_count(Cliente.objects.all()),
            'usuarios_cliente': _safe_count(Usuario.objects.filter(role='cliente')),
            'compra_lineas': _safe_count(CompraProveedorLinea.objects.all()),
            'compras_proveedor': _safe_count(CompraProveedor.objects.all()),
            'movimientos': _safe_count(InventarioMovimiento.objects.all()),
            'stock_fraccionado': _safe_count(StockProductoFraccionado.objects.all()),
            'stock_presentacion': _safe_count(StockPresentacion.objects.all()),
            'presentaciones': _safe_count(Presentacion.objects.all()),
            'productos': _safe_count(Producto.objects.all()),
            'categorias': _safe_count(Categoria.objects.all()),
            'marcas': _safe_count(Marca.objects.all()),
            'proveedores': _safe_count(Proveedor.objects.all()),
            'notificaciones': _safe_count(Notificacion.objects.all()),
            'qb_conflicts': _safe_count(QuickBooksImportConflict.objects.all()),
        }

    def _delete_all(self):
        _safe_delete(NotaAjusteAplicacion.objects.all())
        _safe_delete(NotaAjusteItem.objects.all())
        _safe_delete(NotaAjusteEvidencePhoto.objects.all())
        _safe_delete(NotaAjuste.objects.all())
        _safe_delete(FacturacionRegistroAnulacion.objects.all())
        _safe_delete(DeliveryPayment.objects.all())
        _safe_delete(DeliveryEvidencePhoto.objects.all())
        _safe_delete(DeliveryNotificationLog.objects.all())
        _safe_delete(Delivery.objects.all())
        _safe_delete(InvoiceItem.objects.all())
        _safe_delete(Invoice.objects.all())
        _safe_delete(PedidoEditLock.objects.all())
        _safe_delete(PedidoItem.objects.all())
        _safe_delete(Pedido.objects.all())
        _safe_delete(CotizacionItem.objects.all())
        _safe_delete(Cotizacion.objects.all())
        _safe_delete(ClienteCreditoLimiteAlerta.objects.all())
        deleted_clientes, _ = _safe_delete(Cliente.objects.all())
        deleted_client_users, _ = _safe_delete(Usuario.objects.filter(role='cliente'), allow_raw_delete=False)
        _safe_delete(CompraProveedorLinea.objects.all())
        _safe_delete(CompraProveedor.objects.all())
        _safe_delete(InventarioMovimiento.objects.all())
        _safe_delete(StockProductoFraccionado.objects.all())
        _safe_delete(StockPresentacion.objects.all())
        deleted_presentaciones, _ = _safe_delete(Presentacion.objects.all())
        deleted_productos, _ = _safe_delete(Producto.objects.all())
        deleted_categorias, _ = _safe_delete(Categoria.objects.all())
        deleted_marcas, _ = _safe_delete(Marca.objects.all())
        deleted_proveedores, _ = _safe_delete(Proveedor.objects.all())
        deleted_notificaciones, _ = _safe_delete(Notificacion.objects.all())
        deleted_conflicts, _ = _safe_delete(QuickBooksImportConflict.objects.all())

        connection = QuickBooksConnection.get_solo()
        for cursor_key in QB_CURSOR_KEYS:
            connection.clear_sync_cursor(cursor_key)
        connection.save(update_fields=['sync_state', 'updated_at'])

        self.stdout.write(
            self.style.SUCCESS(
                'Deleted '
                f'{deleted_clientes} customers, {deleted_client_users} customer users, '
                f'{deleted_productos} products, {deleted_presentaciones} presentations, '
                f'{deleted_categorias} categories, {deleted_marcas} brands, '
                f'{deleted_proveedores} suppliers, {deleted_notificaciones} notifications, '
                f'{deleted_conflicts} QuickBooks review rows.'
            )
        )
