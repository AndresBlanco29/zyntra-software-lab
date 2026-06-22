from django.core.management.base import BaseCommand, CommandError

from config.facturacion.models import Invoice
from config.facturacion.services import eliminar_invoice


class Command(BaseCommand):
	help = 'Delete invoices by number and restore inventory when applicable.'

	def add_arguments(self, parser):
		parser.add_argument('numeros', nargs='+', help='Invoice numbers to delete, e.g. INV-2026-003253')
		parser.add_argument(
			'--force',
			action='store_true',
			help='Delete even when the invoice is locked by QuickBooks sync metadata.',
		)

	def handle(self, *args, **options):
		for numero in options['numeros']:
			invoice = Invoice.objects.select_related('pedido', 'cliente', 'delivery').filter(numero=numero).first()
			if invoice is None:
				raise CommandError(f'Invoice not found: {numero}')

			pedido_id = eliminar_invoice(invoice=invoice, force_quickbooks=options['force'])
			self.stdout.write(self.style.SUCCESS(f'Deleted {numero} (order #{pedido_id})'))
