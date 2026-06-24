from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('pedidos', '0009_pedido_edit_lock'),
	]

	operations = [
		migrations.AddField(
			model_name='pedidoitem',
			name='descuento_aplicado',
			field=models.BooleanField(default=False),
		),
		migrations.AddField(
			model_name='pedidoitem',
			name='descuento_monto',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
		),
	]
