from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auditoria', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditlog',
            name='actor_full_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='browser',
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='changes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='device',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='duration_ms',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='geo_city',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='geo_country',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='module',
            field=models.CharField(blank=True, db_index=True, max_length=80),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='os_name',
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name='auditlog',
            name='success',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='action_category',
            field=models.CharField(
                choices=[
                    ('VIEW', 'View'),
                    ('CREATE', 'Create'),
                    ('UPDATE', 'Update'),
                    ('DELETE', 'Delete'),
                    ('ACTION', 'Action'),
                    ('EXPORT', 'Export'),
                    ('LOGIN', 'Login'),
                    ('LOGOUT', 'Logout'),
                    ('SYNC', 'Sync'),
                    ('PRINT', 'Print'),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['entity_type', 'entity_id', '-created_at'], name='auditoria_a_entity__7f0b1a_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['module', '-created_at'], name='auditoria_a_module_2c8f11_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['success', '-created_at'], name='auditoria_a_success_9a1c22_idx'),
        ),
    ]
