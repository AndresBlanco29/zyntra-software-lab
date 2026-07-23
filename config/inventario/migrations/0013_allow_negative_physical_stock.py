from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0012_salida_regalo_movement_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockpresentacion',
            name='stock_fisico',
            field=models.IntegerField(
                default=0,
                help_text='Physical stock counted in presentation packages (boxes). May be negative when QuickBooks reports oversold quantity.',
            ),
        ),
        migrations.AlterField(
            model_name='stockpresentacion',
            name='stock_disponible',
            field=models.IntegerField(
                default=0,
                help_text='Available stock counted in presentation packages (boxes). May be negative when physical stock is oversold.',
            ),
        ),
        migrations.AlterField(
            model_name='inventariomovimiento',
            name='stock_fisico_anterior',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='inventariomovimiento',
            name='stock_fisico_posterior',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='inventariomovimiento',
            name='stock_disponible_anterior',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='inventariomovimiento',
            name='stock_disponible_posterior',
            field=models.IntegerField(default=0),
        ),
    ]
