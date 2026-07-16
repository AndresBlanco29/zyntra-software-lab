from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0026_daily_closing'),
	]

	operations = [
		migrations.AddField(
			model_name='delivery',
			name='motivo_over_payment',
			field=models.TextField(blank=True),
		),
		migrations.AddField(
			model_name='delivery',
			name='motivo_short_payment',
			field=models.TextField(blank=True),
		),
		migrations.AddField(
			model_name='delivery',
			name='over_payment_amount',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
		migrations.AddField(
			model_name='delivery',
			name='short_payment_amount',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
		migrations.AddField(
			model_name='delivery',
			name='payment_balance_delta',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
		migrations.AddField(
			model_name='delivery',
			name='short_payment_evidence',
			field=models.ImageField(blank=True, null=True, upload_to='delivery/short-payment/'),
		),
	]
