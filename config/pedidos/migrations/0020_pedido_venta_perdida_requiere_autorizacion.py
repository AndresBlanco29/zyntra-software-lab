from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0019_pedido_venta_perdida_autorizacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='venta_perdida_requiere_autorizacion',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Order has below-cost lines saved and still needs supervisor '
                    'authorization before invoicing.'
                ),
            ),
        ),
    ]
