from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0028_promocion_imagen'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfiguracionDescuentosPorcentaje',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('descuento_1', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_2', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_3', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_4', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_5', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_6', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_7', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_8', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_9', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
                ('descuento_10', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5)),
            ],
            options={
                'verbose_name': 'Special discount percentage configuration',
                'verbose_name_plural': 'Special discount percentage configuration',
            },
        ),
    ]
