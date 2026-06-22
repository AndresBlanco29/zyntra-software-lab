from django.db import migrations, models

from config.core.migration_utils import add_model_fields_if_missing, build_field


def add_presentacion_costo(apps, schema_editor):
    add_model_fields_if_missing(
        apps,
        schema_editor,
        'productos',
        'Presentacion',
        [
            build_field(
                'costo',
                models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
            ),
        ],
    )


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0011_marca_activo'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_presentacion_costo, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='presentacion',
                    name='costo',
                    field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
                ),
            ],
        ),
    ]
