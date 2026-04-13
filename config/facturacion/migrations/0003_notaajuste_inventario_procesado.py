from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0002_delivery_workflow'),
	]

	operations = [
		migrations.AlterField(
			model_name='notaajuste',
			name='inventario_estado',
			field=models.CharField(choices=[('NO_APLICA', 'Not applicable'), ('PENDIENTE', 'Pending'), ('PROCESADO', 'Processed'), ('ANULADO', 'Cancelled')], default='NO_APLICA', max_length=20),
		),
	]