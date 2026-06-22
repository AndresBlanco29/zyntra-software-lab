from decimal import Decimal

from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



class Migration(migrations.Migration):

	dependencies = [
		('inventario', '0008_compraproveedor_proveedor'),
	]

	operations = wrap_add_field_operations('inventario', [
		migrations.AddField(
			model_name='proveedor',
			name='balance',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
	
	])
