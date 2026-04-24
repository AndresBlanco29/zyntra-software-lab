from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0007_notaajusteevidencephoto'),
	]

	operations = [
		migrations.AlterField(
			model_name='notaajuste',
			name='motivo',
			field=models.CharField(choices=[('DAMAGE', 'Damage'), ('DEFECT', 'Defect'), ('MISSING_ITEM', 'Missing item')], max_length=20),
		),
	]