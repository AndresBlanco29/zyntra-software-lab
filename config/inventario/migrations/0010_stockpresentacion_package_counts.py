from django.db import migrations, models


def convert_reserved_stock_to_packages(apps, schema_editor):
    StockPresentacion = apps.get_model('inventario', 'StockPresentacion')
    for stock in StockPresentacion.objects.select_related('presentacion').iterator():
        unidades = max(int(getattr(stock.presentacion, 'unidades', 0) or 0), 1)
        if unidades > 1 and stock.stock_reservado:
            stock.stock_reservado = stock.stock_reservado // unidades
        stock.stock_disponible = max(int(stock.stock_fisico or 0) - int(stock.stock_reservado or 0), 0)
        stock.save(update_fields=['stock_reservado', 'stock_disponible'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0009_proveedor_balance'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockpresentacion',
            name='stock_disponible',
            field=models.PositiveIntegerField(default=0, help_text='Available stock counted in presentation packages (boxes).'),
        ),
        migrations.AlterField(
            model_name='stockpresentacion',
            name='stock_fisico',
            field=models.PositiveIntegerField(default=0, help_text='Physical stock counted in presentation packages (boxes).'),
        ),
        migrations.AlterField(
            model_name='stockpresentacion',
            name='stock_reservado',
            field=models.PositiveIntegerField(default=0, help_text='Reserved stock counted in presentation packages (boxes).'),
        ),
        migrations.RunPython(convert_reserved_stock_to_packages, migrations.RunPython.noop),
    ]
