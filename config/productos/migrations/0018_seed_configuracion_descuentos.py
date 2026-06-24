from django.db import migrations


def seed_configuracion_descuentos(apps, schema_editor):
    ConfiguracionDescuentos = apps.get_model('productos', 'ConfiguracionDescuentos')
    if ConfiguracionDescuentos.objects.filter(pk=1).exists():
        return

    defaults = {
        'descuento_1': '0.25',
        'descuento_2': '0.50',
        'descuento_3': '0.75',
        'descuento_4': '1.00',
        'descuento_5': '1.50',
        'descuento_6': '2.00',
        'descuento_7': '2.50',
        'descuento_8': '3.00',
        'descuento_9': '4.00',
        'descuento_10': '5.00',
    }
    ConfiguracionDescuentos.objects.create(pk=1, **defaults)


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0017_configuraciondescuentos'),
    ]

    operations = [
        migrations.RunPython(seed_configuracion_descuentos, migrations.RunPython.noop),
    ]
