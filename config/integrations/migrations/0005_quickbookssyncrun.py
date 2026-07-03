from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0004_alter_quickbooksimportconflict_entity_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='QuickBooksSyncRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('trigger', models.CharField(choices=[('MANUAL', 'Manual incremental'), ('MANUAL_FULL', 'Manual full resync'), ('SCHEDULED', 'Scheduled (every 6 hours)')], default='MANUAL', max_length=20)),
                ('status', models.CharField(choices=[('RUNNING', 'Running'), ('SUCCESS', 'Success'), ('PARTIAL', 'Partial success'), ('FAILED', 'Failed'), ('SKIPPED', 'Skipped')], db_index=True, default='RUNNING', max_length=20)),
                ('force_full', models.BooleanField(default=False)),
                ('scheduled_slot', models.CharField(blank=True, db_index=True, max_length=40)),
                ('timezone_name', models.CharField(default='America/New_York', max_length=64)),
                ('summary', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('started_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ('-started_at',),
            },
        ),
    ]
