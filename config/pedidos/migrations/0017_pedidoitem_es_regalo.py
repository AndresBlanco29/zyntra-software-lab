from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0016_pedido_picking_progress'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoitem',
            name='es_regalo',
            field=models.BooleanField(
                default=False,
                help_text='True when this line was added automatically by a Free units cross-product promotion.',
                verbose_name='Free / gift line',
            ),
        ),
        migrations.AddField(
            model_name='pedidoitem',
            name='regalo_origen_item',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='lineas_regalo',
                to='pedidos.pedidoitem',
            ),
        ),
    ]
