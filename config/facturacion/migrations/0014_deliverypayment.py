from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0013_delivery_split_cash_cheque'),
	]

	operations = [
		migrations.CreateModel(
			name='DeliveryPayment',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('position', models.PositiveSmallIntegerField(default=1)),
				('metodo_pago', models.CharField(choices=[('CASH', 'Cash'), ('CHEQUE', 'Cheque'), ('MIXTO', 'Cash + cheque'), ('MULTIPLE', 'Multiple methods'), ('TRANSFERENCIA', 'Transfer'), ('TARJETA', 'Card'), ('ZELLE', 'Zelle'), ('ACH', 'ACH')], max_length=20)),
				('monto', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
				('cheque_numero', models.CharField(blank=True, max_length=80)),
				('cheque_banco', models.CharField(blank=True, max_length=120)),
				('cheque_imagen', models.ImageField(blank=True, null=True, upload_to='delivery/checks/')),
				('transferencia_referencia', models.CharField(blank=True, max_length=120)),
				('tarjeta_ultimos_4', models.CharField(blank=True, max_length=4)),
				('tarjeta_autorizacion', models.CharField(blank=True, max_length=80)),
				('zelle_referencia', models.CharField(blank=True, max_length=120)),
				('zelle_remitente', models.CharField(blank=True, max_length=160)),
				('ach_referencia', models.CharField(blank=True, max_length=120)),
				('ach_cuenta_ultimos_4', models.CharField(blank=True, max_length=4)),
				('created_at', models.DateTimeField(auto_now_add=True)),
				('delivery', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='payments', to='facturacion.delivery')),
			],
			options={'ordering': ('position', 'id')},
		),
		migrations.AddConstraint(
			model_name='deliverypayment',
			constraint=models.UniqueConstraint(fields=('delivery', 'position'), name='unique_delivery_payment_position'),
		),
	]