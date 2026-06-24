from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('notificaciones', '0004_alter_notificacion_tipo'),
	]

	operations = [
		migrations.CreateModel(
			name='WorkspaceDispatchAlertReadState',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('last_opened_at', models.DateTimeField(blank=True, null=True)),
				('updated_at', models.DateTimeField(auto_now=True)),
				('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='dispatch_alert_read_state', to=settings.AUTH_USER_MODEL)),
			],
			options={
				'db_table': 'notificaciones_dispatch_alert_read_state',
			},
		),
	]
