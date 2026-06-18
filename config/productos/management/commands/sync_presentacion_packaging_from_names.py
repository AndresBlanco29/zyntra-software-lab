from django.core.management.base import BaseCommand

from config.productos.models import Producto
from config.productos.packaging import apply_case_packaging_defaults_to_presentacion


class Command(BaseCommand):
    help = 'Detect case packaging from product names and update presentation units/content type.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing presentation names/units even when already configured.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would change without saving.',
        )

    def handle(self, *args, **options):
        overwrite = bool(options['overwrite'])
        dry_run = bool(options['dry_run'])
        updated = 0
        skipped = 0

        for producto in Producto.objects.prefetch_related('presentaciones').iterator(chunk_size=200):
            presentaciones = list(producto.presentaciones.all())
            if not presentaciones:
                skipped += 1
                continue
            target = presentaciones[0]
            if apply_case_packaging_defaults_to_presentacion(target, producto.nombre, overwrite=overwrite):
                updated += 1
                message = (
                    f'{producto.nombre} -> {target.nombre} | '
                    f'unidades={target.unidades} | tipo={target.tipo_contenido}'
                )
                if dry_run:
                    self.stdout.write(f'[dry-run] {message}')
                else:
                    target.save(update_fields=['nombre', 'unidades', 'tipo_contenido'])
                    self.stdout.write(self.style.SUCCESS(message))
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(f'Updated {updated} presentations; skipped {skipped} products.'))
