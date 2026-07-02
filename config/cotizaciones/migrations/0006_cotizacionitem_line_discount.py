from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0005_alter_cotizacion_estado'),
    ]

    operations = [
        migrations.AddField(
            model_name='cotizacionitem',
            name='descuento_aplicado',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='cotizacionitem',
            name='descuento_monto',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]
