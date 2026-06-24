from decimal import Decimal

from django.db import migrations, models


DEFAULT_PRESET_DISCOUNT_AMOUNTS = (
    Decimal('0.25'),
    Decimal('0.50'),
    Decimal('0.75'),
    Decimal('1.00'),
    Decimal('1.50'),
    Decimal('2.00'),
    Decimal('2.50'),
    Decimal('3.00'),
    Decimal('4.00'),
    Decimal('5.00'),
)


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0016_alter_producto_quickbooks_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfiguracionDescuentos',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('descuento_1', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[0], max_digits=10)),
                ('descuento_2', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[1], max_digits=10)),
                ('descuento_3', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[2], max_digits=10)),
                ('descuento_4', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[3], max_digits=10)),
                ('descuento_5', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[4], max_digits=10)),
                ('descuento_6', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[5], max_digits=10)),
                ('descuento_7', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[6], max_digits=10)),
                ('descuento_8', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[7], max_digits=10)),
                ('descuento_9', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[8], max_digits=10)),
                ('descuento_10', models.DecimalField(decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[9], max_digits=10)),
            ],
            options={
                'verbose_name': 'Discount configuration',
                'verbose_name_plural': 'Discount configuration',
            },
        ),
    ]
