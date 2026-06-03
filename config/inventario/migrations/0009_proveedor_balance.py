from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('inventario', '0008_compraproveedor_proveedor'),
	]

	operations = [
		migrations.AddField(
			model_name='proveedor',
			name='balance',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
	]