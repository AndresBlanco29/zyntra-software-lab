from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('pedidos', '0015_pedido_cantidad_pallets'),
	]

	operations = [
		migrations.AddField(
			model_name='pedido',
			name='picking_progress',
			field=models.JSONField(blank=True, default=dict),
		),
		migrations.AddField(
			model_name='pedido',
			name='picking_progress_saved_at',
			field=models.DateTimeField(blank=True, null=True),
		),
	]
