from django.db import migrations


def ensure_permission_overrides_column(apps, schema_editor):
    Usuario = apps.get_model('usuarios', 'Usuario')
    table_name = Usuario._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(cursor, table_name)
        }

    if 'permission_overrides' in existing_columns:
        return

    field = Usuario._meta.get_field('permission_overrides')
    schema_editor.add_field(Usuario, field)


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0008_alter_usuario_role_driver'),
    ]

    operations = [
        migrations.RunPython(ensure_permission_overrides_column, migrations.RunPython.noop),
    ]