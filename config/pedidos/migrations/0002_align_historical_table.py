from django.db import migrations, models


def _existing_columns(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(cursor, table_name)
    return {column.name for column in description}


def align_pedidos_table(apps, schema_editor):
    table_name = 'pedidos_pedidocompra'
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