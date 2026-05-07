from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0012_repair_notaajuste_invoice_nullable'),
	]

	operations = [
		migrations.AddField(
			model_name='delivery',
			name='cheque_imagen',
			field=models.ImageField(blank=True, null=True, upload_to='delivery/checks/'),
		),
		migrations.AddField(
			model_name='delivery',
			name='monto_pagado_cash',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
		migrations.AddField(
			model_name='delivery',
			name='monto_pagado_cheque',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
	]