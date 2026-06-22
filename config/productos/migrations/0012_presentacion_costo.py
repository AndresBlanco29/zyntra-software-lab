from django.db import migrations, models


def add_missing_presentacion_costo(apps, schema_editor):
    model = apps.get_model('productos', 'Presentacion')
    table_name = model._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)
        }

    if 'costo' in existing_columns:
        return

    field = models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)
    field.set_attributes_from_name('costo')
    schema_editor.add_field(model, field)


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0011_marca_activo'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_missing_presentacion_costo, migrations.RunPython.noop),
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
