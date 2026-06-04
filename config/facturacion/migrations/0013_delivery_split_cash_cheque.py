from decimal import Decimal

from django.db import migrations, models


def add_missing_delivery_payment_split_fields(apps, schema_editor):
	model = apps.get_model('facturacion', 'Delivery')
	table_name = model._meta.db_table

	with schema_editor.connection.cursor() as cursor:
		existing_columns = {
			column.name
			for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)
		}

	field_definitions = (
		('cheque_imagen', models.ImageField(blank=True, null=True, upload_to='delivery/checks/')),
		('monto_pagado_cash', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
		('monto_pagado_cheque', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
	)

	for field_name, field in field_definitions:
		if field_name in existing_columns:
			continue
		field.set_attributes_from_name(field_name)
		schema_editor.add_field(model, field)


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0012_repair_notaajuste_invoice_nullable'),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			database_operations=[
				migrations.RunPython(add_missing_delivery_payment_split_fields, migrations.RunPython.noop),
			],
			state_operations=[
				migrations.AddField(
					model_name='delivery',
					name='cheque_imagen',
					field=models.ImageField(blank=True, null=True, upload_to='delivery/checks/'),
				),
				migrations.AddField(
					model_name='delivery',
					name='monto_pagado_cash',
					field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
				),
				migrations.AddField(
					model_name='delivery',
					name='monto_pagado_cheque',
					field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
				),
			],
		),
	]