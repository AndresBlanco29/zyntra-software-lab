from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('clientes', '0006_alter_cliente_nivel_precio'),
	]

	operations = [
		migrations.AddField(
			model_name='cliente',
			name='balance',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
	]