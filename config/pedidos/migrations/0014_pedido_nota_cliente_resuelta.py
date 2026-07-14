from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('pedidos', '0013_pedido_partial_orders'),
	]

	operations = [
		migrations.AddField(
			model_name='pedido',
			name='nota_cliente_resuelta',
			field=models.BooleanField(default=True),
		),
	]
