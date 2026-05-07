from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('productos', '0001_initial'),
		('inventario', '0002_operational_inventory'),
	]

	operations = [
		migrations.CreateModel(
			name='StockProductoFraccionado',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('contenido', models.CharField(max_length=50)),
				('stock_fisico', models.PositiveIntegerField(default=0)),
				('actualizado_en', models.DateTimeField(auto_now=True)),
				('producto', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='stocks_fraccionados', to='productos.producto')),
			],
			options={
				'ordering': ('producto__nombre', 'contenido'),
				'unique_together': {('producto', 'contenido')},
			},
		),
	]