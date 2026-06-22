from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



def seed_reserved_inventory(apps, schema_editor):
	PedidoItem = apps.get_model('pedidos', 'PedidoItem')
	for item in PedidoItem.objects.all().iterator():
		if not item.cantidad_reservada_inventario:
			item.cantidad_reservada_inventario = item.cantidad_solicitada or item.cantidad
			item.save(update_fields=['cantidad_reservada_inventario'])


class Migration(migrations.Migration):

	dependencies = [
		('pedidos', '0006_pedido_estado_invoice_generada'),
	]

	operations = wrap_add_field_operations('pedidos', [
		migrations.AddField(
			model_name='pedidoitem',
			name='cantidad_inventario_aplicada',
			field=models.PositiveIntegerField(default=0),
		),
		migrations.AddField(
			model_name='pedidoitem',
			name='cantidad_reservada_inventario',
			field=models.PositiveIntegerField(default=0),
		),
		migrations.RunPython(seed_reserved_inventory, migrations.RunPython.noop),
	
	])
