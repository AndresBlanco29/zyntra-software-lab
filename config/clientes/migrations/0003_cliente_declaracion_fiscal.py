from django.db import migrations, models

from config.core.migration_utils import add_model_fields_if_missing, build_field


def add_declaracion_fiscal_fields(apps, schema_editor):
    add_model_fields_if_missing(
        apps,
        schema_editor,
        'clientes',
        'Cliente',
        [
            build_field('declaracion_fiscal_aceptada', models.BooleanField(default=False)),
            build_field('declaracion_fiscal_aceptada_en', models.DateTimeField(blank=True, null=True)),
        ],
    )


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0002_cliente_codigo_postal_cliente_pais'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_declaracion_fiscal_fields, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='cliente',
                    name='declaracion_fiscal_aceptada',
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='declaracion_fiscal_aceptada_en',
                    field=models.DateTimeField(blank=True, null=True),
                ),
            ],
        ),
    ]
