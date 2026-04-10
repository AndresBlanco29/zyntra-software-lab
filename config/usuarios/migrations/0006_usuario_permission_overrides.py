from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0005_alter_usuario_role_backoffice'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuario',
            name='permission_overrides',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]