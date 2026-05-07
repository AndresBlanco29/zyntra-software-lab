from decimal import Decimal

from django.db import migrations, models
import django.utils.timezone


def migrate_existing_note_values(apps, schema_editor):
	NotaAjuste = apps.get_model('facturacion', 'NotaAjuste')
	for note in NotaAjuste.objects.all():
		note.monto = note.total
		note.tipo_ajuste = 'PRODUCTO' if note.invoice_id else 'FINANCIERO'
		if note.invoice_id and note.estado == 'APROBADA':
			if note.tipo_documento == 'CREDITO':
				note.monto_aplicado_invoice = note.total
			else:
				note.monto_aplicado_invoice = note.total
		if note.fecha is None:
			note.fecha = note.creada_en or django.utils.timezone.now()
		fields = ['monto', 'tipo_ajuste', 'monto_aplicado_invoice', 'fecha']
		if note.monto_aplicado_cliente != Decimal('0.00'):
			fields.append('monto_aplicado_cliente')
		note.save(update_fields=fields)


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0009_notaajuste_cliente'),
	]

	operations = [
		migrations.AddField(
			model_name='invoice',
			name='credito_cliente_aplicado',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
		migrations.AddField(
			model_name='notaajuste',
			name='fecha',
			field=models.DateTimeField(default=django.utils.timezone.now),
		),
		migrations.AddField(
			model_name='notaajuste',
			name='monto',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
		migrations.AddField(
			model_name='notaajuste',
			name='monto_aplicado_cliente',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
		migrations.AddField(
			model_name='notaajuste',
			name='monto_aplicado_invoice',
			field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
		),
		migrations.AddField(
			model_name='notaajuste',
			name='tipo_ajuste',
			field=models.CharField(choices=[('PRODUCTO', 'Product'), ('FINANCIERO', 'Financial')], default='PRODUCTO', max_length=20),
		),
		migrations.RunPython(migrate_existing_note_values, migrations.RunPython.noop),
		migrations.AlterField(
			model_name='notaajuste',
			name='cliente',
			field=models.ForeignKey(on_delete=models.PROTECT, related_name='notas_ajuste', to='clientes.cliente'),
		),
	]