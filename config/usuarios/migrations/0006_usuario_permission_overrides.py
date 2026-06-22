from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0005_alter_usuario_role_backoffice'),
    ]

    operations = wrap_add_field_operations('usuarios', [
        migrations.AddField(
            model_name='usuario',
            name='permission_overrides',
            field=models.JSONField(blank=True, default=dict),
        ),
    
    ])
