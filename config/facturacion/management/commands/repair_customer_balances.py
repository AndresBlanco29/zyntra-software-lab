from decimal import Decimal

from django.core.management.base import BaseCommand

from config.clientes.models import Cliente
from config.facturacion.services import (
	_customer_has_open_invoice_saldo,
	_sum_operational_open_invoice_outstanding,
)


class Command(BaseCommand):
	help = 'Reset corrupted customer balances that no longer have open invoice debt.'

	def add_arguments(self, parser):
		parser.add_argument(
			'--cliente-id',
			type=int,
			help='Repair a single customer by id.',
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Show which balances would be reset without saving changes.',
		)

	def handle(self, *args, **options):
		queryset = Cliente.objects.all().order_by('id')
		cliente_id = options.get('cliente_id')
		if cliente_id:
			queryset = queryset.filter(id=cliente_id)

		updated = 0
		for cliente in queryset:
			operational_open = _sum_operational_open_invoice_outstanding(cliente=cliente)
			if operational_open > 0:
				continue
			if _customer_has_open_invoice_saldo(cliente=cliente):
				continue
			if Decimal(str(cliente.balance or '0.00')) == Decimal('0.00'):
				continue

			message = (
				f'Cliente #{cliente.id} {cliente.nombre_empresa}: '
				f'balance {cliente.balance} -> 0.00'
			)
			if options['dry_run']:
				self.stdout.write(message)
				continue

			cliente.balance = Decimal('0.00')
			cliente.save(update_fields=['balance'])
			updated += 1
			self.stdout.write(self.style.SUCCESS(message))

		if options['dry_run']:
			self.stdout.write('Dry run complete.')
		else:
			self.stdout.write(self.style.SUCCESS(f'Repaired {updated} customer balance(s).'))
