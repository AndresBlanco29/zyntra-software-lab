from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	initial = True

	dependencies = [
		('productos', '0014_alter_configuracionprecios_options'),
	]

	operations = [
		migrations.CreateModel(
			name='MovimientoInventarioPendiente',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('origen_tipo', models.CharField(max_length=30)),
				('origen_id', models.PositiveBigIntegerField()),
				('referencia', models.CharField(max_length=80)),
				('cantidad', models.PositiveIntegerField(default=1)),
				('direccion', models.CharField(choices=[('ENTRADA', 'Entry'), ('SALIDA', 'Exit')], max_length=20)),
				('estado', models.CharField(choices=[('PENDIENTE', 'Pending'), ('PROCESADO', 'Processed'), ('ANULADO', 'Cancelled')], default='PENDIENTE', max_length=20)),
				('observacion', models.TextField(blank=True)),
				('creada_en', models.DateTimeField(auto_now_add=True)),
				('presentacion', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='movimientos_pendientes', to='productos.presentacion')),
			],
			options={'ordering': ('-creada_en',)},
		),
	]