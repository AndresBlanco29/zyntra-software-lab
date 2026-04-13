from django.db import migrations


def ensure_canal_toma(apps, schema_editor):
    table_name = 'pedidos_pedidocompra'
    with schema_editor.connection.cursor() as cursor:
        existing_tables = set(schema_editor.connection.introspection.table_names(cursor))
        if table_name not in existing_tables:
            return

        description = schema_editor.connection.introspection.get_table_description(cursor, table_name)
        exists = any(column.name == 'canal_toma' for column in description)
        if not exists:
            cursor.execute(
                f"ALTER TABLE {schema_editor.quote_name(table_name)} ADD COLUMN canal_toma varchar(20) NOT NULL DEFAULT ''"
            )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0002_align_historical_table'),
    ]

    operations = [
        migrations.RunPython(ensure_canal_toma, reverse_code=noop_reverse),
    ]