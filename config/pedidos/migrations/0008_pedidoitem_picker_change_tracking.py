from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



class Migration(migrations.Migration):

	dependencies = [
		('productos', '0011_marca_activo'),
		('pedidos', '0007_pedidoitem_inventory_tracking'),
	]

	operations = wrap_add_field_operations('pedidos', [
		migrations.AddField(
			model_name='pedidoitem',
			name='selector_added_by_picker',
			field=models.BooleanField(default=False),
		),
		migrations.AddField(
			model_name='pedidoitem',
			name='selector_original_presentacion',
			field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='pedido_items_selector_originales', to='productos.presentacion'),
		),
	
	])
