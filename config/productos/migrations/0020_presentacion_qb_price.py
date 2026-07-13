from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0019_presentacion_peso_por_caja'),
    ]

    operations = [
        migrations.AddField(
            model_name='presentacion',
            name='qb_price',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Sales price imported from QuickBooks (Sales Price / UnitPrice).',
                max_digits=10,
                null=True,
                verbose_name='QB-PRICE',
            ),
        ),
    ]
