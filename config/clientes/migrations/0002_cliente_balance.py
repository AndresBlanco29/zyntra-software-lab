from decimal import Decimal

from django.db import migrations, models

from config.core.migration_utils import add_model_fields_if_missing, build_field


def add_balance_field(apps, schema_editor):
    add_model_fields_if_missing(
        apps,
        schema_editor,
        'clientes',
        'Cliente',
        [
            build_field(
                'balance',
                models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
            ),
        ],
    )


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0006_alter_cliente_nivel_precio'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_balance_field, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='cliente',
                    name='balance',
                    field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
                ),
            ],
        ),
    ]
