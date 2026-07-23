from django.db import migrations


def _force_signed_stock_columns(apps, schema_editor):
    """Ensure MySQL columns accept negative QuickBooks QtyOnHand values.

    PositiveIntegerField historically created UNSIGNED columns. Django AlterField
    to IntegerField may already be recorded as applied while the DB column is
    still UNSIGNED, which silently stores -10 as 0.
    """
    if schema_editor.connection.vendor != 'mysql':
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            ALTER TABLE inventario_stockpresentacion
                MODIFY stock_fisico INTEGER NOT NULL DEFAULT 0,
                MODIFY stock_disponible INTEGER NOT NULL DEFAULT 0
            """
        )
        cursor.execute(
            """
            ALTER TABLE inventario_inventariomovimiento
                MODIFY stock_fisico_anterior INTEGER NOT NULL DEFAULT 0,
                MODIFY stock_fisico_posterior INTEGER NOT NULL DEFAULT 0,
                MODIFY stock_disponible_anterior INTEGER NOT NULL DEFAULT 0,
                MODIFY stock_disponible_posterior INTEGER NOT NULL DEFAULT 0
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0013_allow_negative_physical_stock'),
    ]

    operations = [
        migrations.RunPython(_force_signed_stock_columns, migrations.RunPython.noop),
    ]
