from django.db import migrations, models
import django.db.models.deletion


def make_usuario_nullable(apps, schema_editor):
    Notificacion = apps.get_model('notificaciones', 'Notificacion')
    Usuario = apps.get_model('usuarios', 'Usuario')

    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(
            cursor,
            Notificacion._meta.db_table,
        )

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
        return

    old_field = models.ForeignKey(
        Usuario,
        on_delete=django.db.models.deletion.SET_NULL,
        null=False,
        blank=True,
        related_name='notificaciones+',
    )
    new_field = models.ForeignKey(
        Usuario,
        on_delete=django.db.models.deletion.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones+',
    )
    old_field.set_attributes_from_name('usuario')
    new_field.set_attributes_from_name('usuario')

    schema_editor.alter_field(Notificacion, old_field, new_field, strict=False)


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('notificaciones', '0002_align_existing_table'),
    ]

    operations = [
        migrations.RunPython(make_usuario_nullable, reverse_code=noop_reverse),
    ]