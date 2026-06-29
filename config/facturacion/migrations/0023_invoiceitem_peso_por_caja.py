from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0022_invoiceitem_descuento_monto_unitario'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoiceitem',
            name='peso_por_caja',
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True),
        ),
    ]
