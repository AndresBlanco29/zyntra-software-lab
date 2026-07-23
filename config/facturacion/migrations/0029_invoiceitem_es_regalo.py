from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0028_delivery_sent_to_driver_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoiceitem',
            name='es_regalo',
            field=models.BooleanField(
                default=False,
                help_text='True when this invoice line comes from a Free units promotional gift.',
                verbose_name='Free / gift line',
            ),
        ),
    ]
