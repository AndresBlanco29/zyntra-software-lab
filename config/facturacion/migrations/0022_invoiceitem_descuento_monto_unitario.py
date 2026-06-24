from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0021_void_records_and_invoice_void_fields'),
	]

	operations = [
		migrations.AddField(
			model_name='invoiceitem',
			name='descuento_monto_unitario',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
		),
	]
