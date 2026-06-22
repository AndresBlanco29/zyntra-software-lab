from django.db import migrations, models

from config.core.migration_utils import add_model_fields_if_missing, build_field


def add_nivel_precio_field(apps, schema_editor):
    add_model_fields_if_missing(
        apps,
        schema_editor,
        'clientes',
        'Cliente',
        [
            build_field(
                'nivel_precio',
                models.PositiveSmallIntegerField(
                    choices=[(1, 'Precio 1'), (2, 'Precio 2'), (3, 'Precio 3'), (4, 'Precio 4'), (5, 'Precio 5')],
                    default=1,
                ),
            ),
        ],
    )


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0004_cliente_review_workflow'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_nivel_precio_field, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='cliente',
                    name='nivel_precio',
                    field=models.PositiveSmallIntegerField(
                        choices=[(1, 'Precio 1'), (2, 'Precio 2'), (3, 'Precio 3'), (4, 'Precio 4'), (5, 'Precio 5')],
                        default=1,
                    ),
                ),
            ],
        ),
    ]
