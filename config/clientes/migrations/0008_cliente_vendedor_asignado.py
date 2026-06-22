import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from config.core.migration_utils import add_model_fields_if_missing, build_field


def add_vendedor_asignado_fields(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    add_model_fields_if_missing(
        apps,
        schema_editor,
        'clientes',
        'Cliente',
        [
            build_field(
                'vendedor_asignado',
                models.ForeignKey(
                    blank=True,
                    limit_choices_to={'role': 'vendedor'},
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='clientes_asignados',
                    to=User,
                ),
            ),
            build_field('vendedor_asignado_en', models.DateTimeField(blank=True, null=True)),
            build_field(
                'vendedor_asignado_por',
                models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='clientes_asignados_por',
                    to=User,
                ),
            ),
        ],
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('clientes', '0007_cliente_quickbooks_sync_fields'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_vendedor_asignado_fields, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='cliente',
                    name='vendedor_asignado',
                    field=models.ForeignKey(
                        blank=True,
                        limit_choices_to={'role': 'vendedor'},
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='clientes_asignados',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='vendedor_asignado_en',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='vendedor_asignado_por',
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='clientes_asignados_por',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
