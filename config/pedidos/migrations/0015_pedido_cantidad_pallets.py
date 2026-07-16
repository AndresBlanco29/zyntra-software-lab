from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0014_pedido_nota_cliente_resuelta'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='cantidad_pallets',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Pallet count entered by the picker when verification finishes.',
                max_digits=8,
                null=True,
            ),
        ),
    ]
