from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0010_invoice_customer_credit_and_note_fields'),
	]

	operations = [
		migrations.AddField(
			model_name='notaajusteitem',
			name='contenido_fraccionado',
			field=models.CharField(blank=True, max_length=50),
		),
	]