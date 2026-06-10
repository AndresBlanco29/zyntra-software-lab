from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.facturacion.models import (
    Delivery,
    DeliveryEvidencePhoto,
    DeliveryNotificationLog,
    DeliveryPayment,
    Invoice,
    InvoiceItem,
    NotaAjuste,
    NotaAjusteAplicacion,
)
from config.integrations.models import QuickBooksConnection, QuickBooksImportConflict
from config.pedidos.models import Pedido, PedidoItem
from config.usuarios.models import Usuario


CUSTOMER_QB_CONFLICT_TYPES = (
    QuickBooksImportConflict.ENTITY_CUSTOMER,
    QuickBooksImportConflict.ENTITY_INVOICE,
    QuickBooksImportConflict.ENTITY_CREDIT_MEMO,
)


class Command(BaseCommand):
    help = (
        'Delete all customers and their dependent sales records so QuickBooks customer import '
        'can start from a clean slate. Does not touch products, inventory, or suppliers.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            required=True,
            help='Type CLEAR_CUSTOMERS to confirm deletion.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show counts only; do not delete anything.',
        )

    def handle(self, *args, **options):
        if str(options.get('confirm') or '').strip().upper() != 'CLEAR_CUSTOMERS':
            raise CommandError('Cancelled. Re-run with --confirm=CLEAR_CUSTOMERS')

        counts = {
            'nota_aplicaciones': NotaAjusteAplicacion.objects.count(),
            'notas_ajuste': NotaAjuste.objects.count(),
            'delivery_payments': DeliveryPayment.objects.count(),
            'delivery_evidence': DeliveryEvidencePhoto.objects.count(),
            'delivery_notifications': DeliveryNotificationLog.objects.count(),
            'deliveries': Delivery.objects.count(),
            'invoice_items': InvoiceItem.objects.count(),
            'invoices': Invoice.objects.count(),
            'pedido_items': PedidoItem.objects.count(),
            'pedidos': Pedido.objects.count(),
            'cotizacion_items': CotizacionItem.objects.count(),
            'cotizaciones': Cotizacion.objects.count(),
            'qb_customer_conflicts': QuickBooksImportConflict.objects.filter(
                entity_type__in=CUSTOMER_QB_CONFLICT_TYPES,
            ).count(),
            'clientes': Cliente.objects.count(),
            'usuarios_cliente': Usuario.objects.filter(role='cliente').count(),
        }
        self.stdout.write('Customer-related rows to remove:')
        for label, value in counts.items():
            self.stdout.write(f'  - {label}: {value}')

        if options.get('dry_run'):
            self.stdout.write(self.style.WARNING('Dry run only. No data was deleted.'))
            return

        with transaction.atomic():
            NotaAjusteAplicacion.objects.all().delete()
            NotaAjuste.objects.all().delete()
            DeliveryPayment.objects.all().delete()
            DeliveryEvidencePhoto.objects.all().delete()
            DeliveryNotificationLog.objects.all().delete()
            Delivery.objects.all().delete()
            InvoiceItem.objects.all().delete()
            Invoice.objects.all().delete()
            PedidoItem.objects.all().delete()
            Pedido.objects.all().delete()
            CotizacionItem.objects.all().delete()
            Cotizacion.objects.all().delete()
            deleted_conflicts, _ = QuickBooksImportConflict.objects.filter(
                entity_type__in=CUSTOMER_QB_CONFLICT_TYPES,
            ).delete()
            deleted_clientes, _ = Cliente.objects.all().delete()
            deleted_usuarios, _ = Usuario.objects.filter(role='cliente').delete()

            connection = QuickBooksConnection.get_solo()
            connection.clear_sync_cursor('quickbooks:customer')

        self.stdout.write(
            self.style.SUCCESS(
                'Customers cleared: '
                f'{deleted_clientes} customers, {deleted_usuarios} customer users, '
                f'{deleted_conflicts} QuickBooks review rows.'
            )
        )
        self.stdout.write('You can now import customers from QuickBooks.')
