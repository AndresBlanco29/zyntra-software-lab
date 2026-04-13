from django.db import migrations, models


def _existing_tables(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        return set(schema_editor.connection.introspection.table_names(cursor))


def _existing_columns(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(cursor, table_name)
    return {column.name for column in description}


def align_pedidos_table(apps, schema_editor):
    table_name = 'pedidos_pedidocompra'
    default_table_name = 'pedidos_pedido'
    existing_tables = _existing_tables(schema_editor)

    if table_name not in existing_tables and default_table_name in existing_tables:
        schema_editor.execute(
            f"ALTER TABLE {schema_editor.quote_name(default_table_name)} RENAME TO {schema_editor.quote_name(table_name)}"
        )
        existing_tables = _existing_tables(schema_editor)

    if table_name not in existing_tables:
        return

    existing_columns = _existing_columns(schema_editor, table_name)

    if 'canal_toma' not in existing_columns:
        schema_editor.execute(
            "ALTER TABLE pedidos_pedidocompra ADD COLUMN canal_toma varchar(20) NOT NULL DEFAULT ''"
        )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(align_pedidos_table, reverse_code=noop_reverse),
            ],
            state_operations=[
                migrations.AlterModelTable(
                    name='pedido',
                    table='pedidos_pedidocompra',
                ),
            ],
        ),
    ]