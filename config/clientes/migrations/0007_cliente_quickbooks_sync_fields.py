from django.db import migrations, models

from config.core.migration_utils import add_model_fields_if_missing, build_field


def add_quickbooks_sync_fields(apps, schema_editor):
    add_model_fields_if_missing(
        apps,
        schema_editor,
        'clientes',
        'Cliente',
        [
            build_field('last_synced_at', models.DateTimeField(blank=True, null=True)),
            build_field('quickbooks_id', models.CharField(blank=True, db_index=True, max_length=100, null=True)),
            build_field(
                'sync_status',
                models.CharField(
                    choices=[('PENDING', 'Pending'), ('SYNCED', 'Synced'), ('FAILED', 'Failed')],
                    db_index=True,
                    default='PENDING',
                    max_length=20,
                ),
            ),
        ],
    )


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0002_cliente_balance'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_quickbooks_sync_fields, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='cliente',
                    name='last_synced_at',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='quickbooks_id',
                    field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
                ),
                migrations.AddField(
                    model_name='cliente',
                    name='sync_status',
                    field=models.CharField(
                        choices=[('PENDING', 'Pending'), ('SYNCED', 'Synced'), ('FAILED', 'Failed')],
                        db_index=True,
                        default='PENDING',
                        max_length=20,
                    ),
                ),
            ],
        ),
    ]
