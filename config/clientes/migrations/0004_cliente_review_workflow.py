import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from config.core.migration_utils import add_model_fields_if_missing, build_field, existing_table_columns


def populate_review_status(apps, schema_editor):
    Cliente = apps.get_model('clientes', 'Cliente')

    Cliente.objects.filter(aprobado=True).update(estado_revision='APROBADO')
    Cliente.objects.filter(aprobado=False).update(estado_revision='PENDIENTE')

    for cliente in Cliente.objects.filter(correction_token__isnull=True).iterator():
        cliente.correction_token = uuid.uuid4()
        cliente.save(update_fields=['correction_token'])


def add_review_workflow_fields(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    add_model_fields_if_missing(
        apps,
        schema_editor,
        'clientes',
        'Cliente',
        [
            build_field('adjunto_rechazo', models.FileField(blank=True, null=True, upload_to='certificados/rechazos/')),
            build_field('aprobado_en', models.DateTimeField(blank=True, null=True)),
            build_field(
                'aprobado_por',
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='clientes_aprobados_admin',
                    to=User,
                ),
            ),
            build_field('corrected_at', models.DateTimeField(blank=True, null=True)),
            build_field('correction_requested_at', models.DateTimeField(blank=True, null=True)),
            build_field('correction_token', models.UUIDField(blank=True, editable=False, null=True)),
            build_field(
                'estado_revision',
                models.CharField(
                    choices=[('PENDIENTE', 'Pendiente'), ('APROBADO', 'Aprobado'), ('RECHAZADO', 'Rechazado')],
                    db_index=True,
                    default='PENDIENTE',
                    max_length=20,
                ),
            ),
            build_field('nota_rechazo', models.TextField(blank=True, default='')),
            build_field('rechazado_en', models.DateTimeField(blank=True, null=True)),
            build_field(
                'rechazado_por',
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='clientes_rechazados_admin',
                    to=User,
                ),
            ),
        ],
    )


def ensure_correction_token_unique(apps, schema_editor):
    model = apps.get_model('clientes', 'Cliente')
    table_name = model._meta.db_table
    if 'correction_token' not in existing_table_columns(schema_editor, table_name):
        return

    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(cursor, table_name)

    for meta in constraints.values():
        if meta.get('unique') and 'correction_token' in (meta.get('columns') or []):
            return

    old_field = models.UUIDField(blank=True, editable=False, null=True)
    old_field.set_attributes_from_name('correction_token')
    new_field = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    new_field.set_attributes_from_name('correction_token')
    schema_editor.alter_field(model, old_field, new_field)


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0003_cliente_declaracion_fiscal'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_review_workflow_fields, migrations.RunPython.noop),
                migrations.RunPython(populate_review_status, migrations.RunPython.noop),
                migrations.RunPython(ensure_correction_token_unique, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='cliente',
                    name='adjunto_rechazo',
                    field=models.FileField(blank=True, null=True, upload_to='certificados/rechazos/'),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='aprobado_en',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='aprobado_por',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clientes_aprobados_admin', to=settings.AUTH_USER_MODEL),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='corrected_at',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='correction_requested_at',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='correction_token',
                    field=models.UUIDField(blank=True, editable=False, null=True),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='estado_revision',
                    field=models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('APROBADO', 'Aprobado'), ('RECHAZADO', 'Rechazado')], db_index=True, default='PENDIENTE', max_length=20),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='nota_rechazo',
                    field=models.TextField(blank=True, default=''),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='rechazado_en',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='rechazado_por',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clientes_rechazados_admin', to=settings.AUTH_USER_MODEL),
                ),
                migrations.RunPython(populate_review_status, migrations.RunPython.noop),
                migrations.AlterField(
                    model_name='cliente',
                    name='correction_token',
                    field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
            ],
        ),
    ]
