from django.core.management.base import BaseCommand

from config.clientes.models import Cliente
from config.clientes.phone import normalize_stored_phone_number


class Command(BaseCommand):
    help = 'Normalize stored customer phone numbers to exactly 10 digits.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--cliente-id',
            type=int,
            help='Repair a single customer by id.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show which phone numbers would change without saving.',
        )

    def handle(self, *args, **options):
        queryset = Cliente.objects.all().order_by('id')
        cliente_id = options.get('cliente_id')
        if cliente_id:
            queryset = queryset.filter(id=cliente_id)

        updated = 0
        for cliente in queryset:
            normalized = normalize_stored_phone_number(cliente.telefono)
            if not normalized or normalized == cliente.telefono:
                continue

            message = f'Cliente #{cliente.id}: {cliente.telefono!r} -> {normalized!r}'
            if options['dry_run']:
                self.stdout.write(message)
                continue

            cliente.telefono = normalized
            cliente.save(update_fields=['telefono'])
            updated += 1
            self.stdout.write(self.style.SUCCESS(message))

        if options['dry_run']:
            self.stdout.write('Dry run complete.')
        else:
            self.stdout.write(self.style.SUCCESS(f'Repaired {updated} customer phone number(s).'))
