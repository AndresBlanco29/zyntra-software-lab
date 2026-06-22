from django.core.management.base import BaseCommand, CommandError

from config.facturacion.models import NotaAjuste
from config.facturacion.services import eliminar_nota_ajuste


class Command(BaseCommand):
	help = 'Delete adjustment notes by number and reverse balances or inventory when applicable.'

	def add_arguments(self, parser):
		parser.add_argument('numeros', nargs='+', help='Note numbers to delete, e.g. DBN-2026-000072')
		parser.add_argument(
			'--force',
			action='store_true',
			help='Delete even when the parent invoice is locked by QuickBooks sync metadata.',
		)

	def handle(self, *args, **options):
		for numero in options['numeros']:
			nota = NotaAjuste.objects.select_related('invoice', 'cliente').filter(numero=numero).first()
			if nota is None:
				raise CommandError(f'Adjustment note not found: {numero}')

			if options['force']:
				eliminar_nota_ajuste(nota=nota, force_quickbooks=True)
			else:
				eliminar_nota_ajuste(nota=nota)
			self.stdout.write(self.style.SUCCESS(f'Deleted {numero}'))
