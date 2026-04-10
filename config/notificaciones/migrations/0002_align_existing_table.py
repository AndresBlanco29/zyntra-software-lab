from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('notificaciones', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
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