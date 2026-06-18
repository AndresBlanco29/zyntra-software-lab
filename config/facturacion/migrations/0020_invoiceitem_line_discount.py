from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0019_invoice_quickbooks_payment_status'),
	]

	operations = [
		migrations.AddField(
			model_name='invoiceitem',
			name='descuento_porcentaje',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=5),
		),
		migrations.AddField(
			model_name='invoiceitem',
			name='precio_unitario_lista',
			field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
		),
	]
