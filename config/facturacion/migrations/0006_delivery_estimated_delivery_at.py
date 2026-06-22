from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0005_invoiceitem_precio_venta_sugerido_unitario'),
	]

	operations = wrap_add_field_operations('facturacion', [
		migrations.AddField(
			model_name='delivery',
			name='estimated_delivery_at',
			field=models.DateTimeField(blank=True, null=True),
		),
	
	])
