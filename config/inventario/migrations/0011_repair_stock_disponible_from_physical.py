from django.db import migrations


def repair_stock_disponible(apps, schema_editor):
    StockPresentacion = apps.get_model('inventario', 'StockPresentacion')
    for stock in StockPresentacion.objects.all().iterator():
        expected = max(int(stock.stock_fisico or 0) - int(stock.stock_reservado or 0), 0)
        if int(stock.stock_disponible or 0) != expected:
            stock.stock_disponible = expected
            stock.save(update_fields=['stock_disponible'])


class Migration(migrations.Migration):
    dependencies = [
        ('inventario', '0010_stockpresentacion_package_counts'),
    ]

    operations = [
        migrations.RunPython(repair_stock_disponible, migrations.RunPython.noop),
    ]
