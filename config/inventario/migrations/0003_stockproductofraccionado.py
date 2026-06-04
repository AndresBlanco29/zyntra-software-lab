from django.db import migrations, models


def create_stock_producto_fraccionado_if_missing(apps, schema_editor):
	from config.inventario.models import StockProductoFraccionado

	model = StockProductoFraccionado
	table_name = model._meta.db_table

	with schema_editor.connection.cursor() as cursor:
		tables = set(schema_editor.connection.introspection.table_names(cursor))

	if table_name in tables:
		return

	schema_editor.create_model(model)


class Migration(migrations.Migration):

	dependencies = [
		('productos', '0001_initial'),
		('inventario', '0002_operational_inventory'),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[
				migrations.RunPython(create_stock_producto_fraccionado_if_missing, migrations.RunPython.noop),
			],
			state_operations=[
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
			],
		),
	]