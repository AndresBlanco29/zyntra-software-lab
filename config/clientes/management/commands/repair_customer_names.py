from django.core.management.base import BaseCommand

from config.clientes.models import Cliente
from config.integrations.quickbooks.sync import resolve_customer_company_name


class Command(BaseCommand):
	help = 'Remove historical LTG Customer export prefixes from stored customer names.'

	def add_arguments(self, parser):
		parser.add_argument(
			'--cliente-id',
			type=int,
			help='Repair a single customer by id.',
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Show which names would change without saving.',
		)

	def handle(self, *args, **options):
		queryset = Cliente.objects.all().order_by('id')
		cliente_id = options.get('cliente_id')
		if cliente_id:
			queryset = queryset.filter(id=cliente_id)

		updated = 0
		for cliente in queryset:
			clean_name = resolve_customer_company_name(cliente)
			if clean_name == cliente.nombre_empresa:
				continue

			message = f'Cliente #{cliente.id}: {cliente.nombre_empresa!r} -> {clean_name!r}'
			if options['dry_run']:
				self.stdout.write(message)
				continue

			cliente.nombre_empresa = clean_name
			cliente.save(update_fields=['nombre_empresa'])
			updated += 1
			self.stdout.write(self.style.SUCCESS(message))

		if options['dry_run']:
			self.stdout.write('Dry run complete.')
		else:
			self.stdout.write(self.style.SUCCESS(f'Repaired {updated} customer name(s).'))
