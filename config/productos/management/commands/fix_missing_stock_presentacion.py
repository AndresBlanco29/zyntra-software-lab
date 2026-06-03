from django.core.management.base import BaseCommand
from django.db import transaction
from config.productos.models import Presentacion
from config.inventario.models import StockPresentacion


class Command(BaseCommand):
    help = 'Crea StockPresentacion para las presentaciones que no lo tienen (en caso de que hayan sido importadas de QB antes de crear el signal)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Procesar TODAS las presentaciones, no solo las de QuickBooks',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostrar qué se haría sin hacer cambios reales',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        process_all = options.get('all', False)
        
        # Filtrar presentaciones que necesitan StockPresentacion
        if process_all:
            queryset = Presentacion.objects.all()
            self.stdout.write("📦 Buscando TODAS las presentaciones sin StockPresentacion...")
        else:
            queryset = Presentacion.objects.filter(quickbooks_id__isnull=False)
            self.stdout.write("📦 Buscando presentaciones de QuickBooks sin StockPresentacion...")
        
        presentaciones_sin_stock = queryset.exclude(stock_operativo__isnull=False)
        count = presentaciones_sin_stock.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS("✅ No hay presentaciones sin StockPresentacion. ¡Excelente!"))
            return
        
        self.stdout.write(f"Found {count} presentaciones sin StockPresentacion\n")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN - No se harán cambios reales\n"))
            self.stdout.write("Presentaciones que serían procesadas:")
            for i, p in enumerate(presentaciones_sin_stock[:10], 1):
                self.stdout.write(f"  {i}. {p.producto.nombre} / {p.nombre} (QB ID: {p.quickbooks_id})")
            if count > 10:
                self.stdout.write(f"  ... y {count - 10} más")
            return
        
        # Crear StockPresentacion para cada una
        created_count = 0
        error_count = 0
        
        with transaction.atomic():
            for i, presentacion in enumerate(presentaciones_sin_stock, 1):
                try:
                    stock, created = StockPresentacion.objects.get_or_create(
                        presentacion=presentacion,
                        defaults={'stock_fisico': 0, 'stock_reservado': 0}
                    )
                    if created:
                        created_count += 1
                        self.stdout.write(f"✅ [{i}/{count}] {presentacion.producto.nombre} / {presentacion.nombre}")
                    else:
                        self.stdout.write(f"ℹ️  [{i}/{count}] {presentacion.producto.nombre} ya tenía stock")
                except Exception as e:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(f"❌ [{i}/{count}] Error con {presentacion.producto.nombre}: {str(e)}")
                    )
        
        # Resumen
        self.stdout.write("\n" + "="*70)
        self.stdout.write(f"✅ StockPresentacion creados: {created_count}")
        self.stdout.write(f"❌ Errores: {error_count}")
        self.stdout.write(f"📊 Total procesados: {created_count + error_count}/{count}")
        self.stdout.write("="*70)
        
        if error_count == 0:
            self.stdout.write(self.style.SUCCESS("\n🎉 ¡Sincronización completada sin errores!"))
        else:
            self.stdout.write(self.style.WARNING(f"\n⚠️  Hubo {error_count} errores. Revisa los detalles arriba."))
