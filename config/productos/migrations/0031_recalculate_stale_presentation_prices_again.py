from django.db import migrations


def recalculate_stale_prices_again(apps, schema_editor):
    # Force another pass in case 0030 did not land on production yet or
    # presentations stayed stale after a partial QuickBooks cost sync.
    from config.productos.models import Presentacion

    for presentacion in Presentacion.objects.exclude(costo__isnull=True).iterator(chunk_size=200):
        presentacion.save(update_fields=['costo'])


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0030_recalculate_stale_presentation_prices'),
    ]

    operations = [
        migrations.RunPython(recalculate_stale_prices_again, noop_reverse),
    ]
