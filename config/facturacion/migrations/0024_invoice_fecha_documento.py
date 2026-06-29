from django.db import migrations, models
from django.utils import timezone


def backfill_invoice_fecha_documento(apps, schema_editor):
	Invoice = apps.get_model('facturacion', 'Invoice')
	for invoice in Invoice.objects.filter(fecha_documento__isnull=True).iterator():
		if not invoice.creada_en:
			continue
		created_at = invoice.creada_en
		if timezone.is_naive(created_at):
			created_at = timezone.make_aware(created_at, timezone.get_current_timezone())
		invoice.fecha_documento = timezone.localtime(created_at).date()
		invoice.save(update_fields=['fecha_documento'])


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0023_invoiceitem_peso_por_caja'),
	]

	operations = [
		migrations.AddField(
			model_name='invoice',
			name='fecha_documento',
			field=models.DateField(blank=True, db_index=True, null=True),
		),
		migrations.RunPython(backfill_invoice_fecha_documento, migrations.RunPython.noop),
	]
