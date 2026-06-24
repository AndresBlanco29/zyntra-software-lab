import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('pedidos', '0008_pedidoitem_picker_change_tracking'),
	]

	operations = [
		migrations.CreateModel(
			name='PedidoEditLock',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('locked_at', models.DateTimeField(auto_now_add=True)),
				('last_seen_at', models.DateTimeField(auto_now=True)),
				('locked_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pedido_edit_locks', to=settings.AUTH_USER_MODEL)),
				('pedido', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='edit_lock', to='pedidos.pedido')),
			],
			options={
				'verbose_name': 'Sales order edit lock',
				'verbose_name_plural': 'Sales order edit locks',
				'db_table': 'pedidos_pedidoeditlock',
			},
		),
	]
