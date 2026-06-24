from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('pedidos', '0010_pedidoitem_line_discount'),
	]

	operations = [
		migrations.AddField(
			model_name='pedido',
			name='credit_limit_liberado',
			field=models.BooleanField(default=False),
		),
		migrations.AddField(
			model_name='pedido',
			name='credit_limit_bloqueado',
			field=models.BooleanField(default=False),
		),
	]
