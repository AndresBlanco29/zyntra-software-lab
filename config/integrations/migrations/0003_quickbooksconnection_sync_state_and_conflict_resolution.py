from django.conf import settings
from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('integrations', '0002_quickbooksimportconflict'),
    ]

    operations = wrap_add_field_operations('integrations', [
        migrations.AddField(
            model_name='quickbooksconnection',
            name='sync_state',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='quickbooksimportconflict',
            name='resolution_note',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='quickbooksimportconflict',
            name='resolved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='quickbooks_conflicts_resolved', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='quickbooksimportconflict',
            name='status',
            field=models.CharField(choices=[('CONFLICT', 'Conflict'), ('MATCHED', 'Matched'), ('DISMISSED', 'Dismissed')], default='CONFLICT', max_length=20),
        ),
    
    ])
