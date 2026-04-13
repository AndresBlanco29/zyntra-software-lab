from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def align_notificaciones_table(apps, schema_editor):
    Notificacion = apps.get_model('notificaciones', 'Notificacion')
    Usuario = apps.get_model('usuarios', 'Usuario')
    table_name = Notificacion._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_tables = set(schema_editor.connection.introspection.table_names(cursor))
        if table_name not in existing_tables:
            return

        description = schema_editor.connection.introspection.get_table_description(cursor, table_name)

    existing_columns = {column.name for column in description}

    if 'usuario_id' not in existing_columns:
        field = models.ForeignKey(
            Usuario,
            on_delete=django.db.models.deletion.SET_NULL,
            null=True,
            blank=True,
            related_name='notificaciones+',
        )
        field.set_attributes_from_name('usuario')
        schema_editor.add_field(Notificacion, field)

    if 'destino' in existing_columns and schema_editor.connection.vendor == 'mysql':
        schema_editor.execute(
            f"ALTER TABLE {schema_editor.quote_name(table_name)} MODIFY {schema_editor.quote_name('destino')} varchar(20) NOT NULL DEFAULT 'BACKOFFICE'"
        )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('notificaciones', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(align_notificaciones_table, reverse_code=noop_reverse),
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name='notificacion',
                    name='destino',
                ),
                migrations.AlterField(
                    model_name='notificacion',
                    name='titulo',
                    field=models.CharField(max_length=160),
                ),
                migrations.AlterField(
                    model_name='notificacion',
                    name='url',
                    field=models.CharField(blank=True, max_length=300),
                ),
                migrations.AddField(
                    model_name='notificacion',
                    name='usuario',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notificaciones', to=settings.AUTH_USER_MODEL),
                ),
            ],
        ),
    ]