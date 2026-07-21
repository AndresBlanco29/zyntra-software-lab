from django.db import migrations, models


def backfill_sent_to_driver_at(apps, schema_editor):
	Delivery = apps.get_model('facturacion', 'Delivery')
	for delivery in Delivery.objects.filter(
		sent_to_driver_at__isnull=True,
		invoice__metodo_entrega='RUTA_DRIVER',
	).select_related('invoice'):
		delivery.sent_to_driver_at = delivery.created_at
		delivery.save(update_fields=['sent_to_driver_at'])


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0027_delivery_over_short_payment'),
	]

	operations = [
		migrations.AddField(
			model_name='delivery',
			name='sent_to_driver_at',
			field=models.DateTimeField(blank=True, db_index=True, null=True),
		),
		migrations.RunPython(backfill_sent_to_driver_at, migrations.RunPython.noop),
	]
