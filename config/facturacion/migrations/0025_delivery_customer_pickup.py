from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('facturacion', '0024_invoice_fecha_documento'),
	]

	operations = [
		migrations.AddField(
			model_name='delivery',
			name='is_customer_pickup',
			field=models.BooleanField(default=False),
		),
		migrations.AddField(
			model_name='delivery',
			name='completed_by',
			field=models.ForeignKey(
				blank=True,
				null=True,
				on_delete=django.db.models.deletion.SET_NULL,
				related_name='pickup_deliveries_completed',
				to=settings.AUTH_USER_MODEL,
			),
		),
		migrations.AlterField(
			model_name='delivery',
			name='driver',
			field=models.ForeignKey(
				blank=True,
				limit_choices_to={'role': 'driver'},
				null=True,
				on_delete=django.db.models.deletion.PROTECT,
				related_name='deliveries',
				to=settings.AUTH_USER_MODEL,
			),
		),
	]
