from django.db import migrations


def _ensure_signed_stock_columns(apps, schema_editor):
    from config.inventario.signed_stock import ensure_signed_stock_columns

    # Force ALTER even if information_schema looks correct: production has
    # already shown UNSIGNED columns after 0014 was marked applied.
    ensure_signed_stock_columns(force=True)


class Migration(migrations.Migration):
    # MySQL DDL (ALTER TABLE) must not run inside an atomic migration block.
    atomic = False

    dependencies = [
        ('inventario', '0016_emergency_inventory_adjustment'),
    ]

    operations = [
        migrations.RunPython(_ensure_signed_stock_columns, migrations.RunPython.noop),
    ]
