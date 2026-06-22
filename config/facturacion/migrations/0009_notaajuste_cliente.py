from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



def populate_note_customer(apps, schema_editor):
	NotaAjuste = apps.get_model('facturacion', 'NotaAjuste')
	for note in NotaAjuste.objects.select_related('invoice__cliente').all():
		if note.cliente_id is None and note.invoice_id:
			note.cliente_id = note.invoice.cliente_id
			note.save(update_fields=['cliente'])


class Migration(migrations.Migration):

	dependencies = [
		('clientes', '0001_initial'),
		('facturacion', '0008_alter_notaajuste_motivo'),
	]

	operations = wrap_add_field_operations('facturacion', [
		migrations.AddField(
			model_name='notaajuste',
			name='cliente',
			field=models.ForeignKey(blank=True, null=True, on_delete=models.PROTECT, related_name='notas_ajuste', to='clientes.cliente'),
		),
		migrations.AlterField(
			model_name='notaajuste',
			name='invoice',
			field=models.ForeignKey(blank=True, null=True, on_delete=models.PROTECT, related_name='notas_ajuste', to='facturacion.invoice'),
		),
		migrations.RunPython(populate_note_customer, migrations.RunPython.noop),
	
	])
