from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0005_invoiceitem_precio_venta_sugerido_unitario'),
	]

	operations = [
		migrations.AddField(
			model_name='delivery',
			name='estimated_delivery_at',
			field=models.DateTimeField(blank=True, null=True),
		),
	]