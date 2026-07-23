import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0006_cotizacionitem_line_discount'),
    ]

    operations = [
        migrations.AddField(
            model_name='cotizacionitem',
            name='es_regalo',
            field=models.BooleanField(
                default=False,
                help_text='True when this line was added automatically by a Free units cross-product promotion.',
                verbose_name='Free / gift line',
            ),
        ),
        migrations.AddField(
            model_name='cotizacionitem',
            name='regalo_origen_item',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='lineas_regalo',
                to='cotizaciones.cotizacionitem',
            ),
        ),
    ]
