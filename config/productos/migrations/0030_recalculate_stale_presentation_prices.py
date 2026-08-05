from django.db import migrations


def recalculate_stale_prices(apps, schema_editor):
    # Use the real model so Presentacion.save() recalculates Price 1-5 from cost.
    from config.productos.models import Presentacion

    for presentacion in Presentacion.objects.exclude(costo__isnull=True).iterator(chunk_size=200):
        presentacion.save(update_fields=['costo'])


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0029_configuraciondescuentosporcentaje'),
    ]

    operations = [
        migrations.RunPython(recalculate_stale_prices, noop_reverse),
    ]
