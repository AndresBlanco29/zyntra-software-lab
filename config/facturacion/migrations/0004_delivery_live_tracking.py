from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0003_notaajuste_inventario_procesado'),
	]

	operations = [
		migrations.AddField(
			model_name='delivery',
			name='current_accuracy_meters',
			field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
		),
		migrations.AddField(
			model_name='delivery',
			name='current_heading',
			field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True),
		),
		migrations.AddField(
			model_name='delivery',
			name='current_latitude',
			field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
		),
		migrations.AddField(
			model_name='delivery',
			name='current_longitude',
			field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
		),
		migrations.AddField(
			model_name='delivery',
			name='current_speed_mps',
			field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
		),
		migrations.AddField(
			model_name='delivery',
			name='location_updated_at',
			field=models.DateTimeField(blank=True, null=True),
		),
	]