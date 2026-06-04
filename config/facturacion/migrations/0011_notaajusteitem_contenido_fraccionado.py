from django.db import migrations, models


def add_missing_contenido_fraccionado(apps, schema_editor):
	model = apps.get_model('facturacion', 'NotaAjusteItem')
	table_name = model._meta.db_table

	with schema_editor.connection.cursor() as cursor:
		existing_columns = {
			column.name
			for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)
		}

	if 'contenido_fraccionado' in existing_columns:
		return

	field = models.CharField(blank=True, max_length=50)
	field.set_attributes_from_name('contenido_fraccionado')
	schema_editor.add_field(model, field)


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0010_invoice_customer_credit_and_note_fields'),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[
				migrations.RunPython(add_missing_contenido_fraccionado, migrations.RunPython.noop),
			],
			state_operations=[
				migrations.AddField(
					model_name='notaajusteitem',
					name='contenido_fraccionado',
					field=models.CharField(blank=True, max_length=50),
				),
			],
		),
	]