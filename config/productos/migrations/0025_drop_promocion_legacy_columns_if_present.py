from django.db import migrations

LEGACY_COLUMNS = ('cantidad_minima', 'tipo_beneficio', 'valor_beneficio')
TABLE_NAME = 'productos_promocion'


def _existing_columns(schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, TABLE_NAME)
    return {column.name for column in description}


def drop_legacy_promocion_columns(apps, schema_editor):
    existing_columns = _existing_columns(schema_editor)
    for column_name in LEGACY_COLUMNS:
        if column_name not in existing_columns:
            continue
        schema_editor.execute(f'ALTER TABLE `{TABLE_NAME}` DROP COLUMN `{column_name}`')


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0024_remove_promocion_legacy_fields'),
    ]

    operations = [
        migrations.RunPython(drop_legacy_promocion_columns, migrations.RunPython.noop),
    ]
