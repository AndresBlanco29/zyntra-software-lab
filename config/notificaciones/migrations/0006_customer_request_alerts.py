from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('notificaciones', '0005_workspacedispatchalertreadstate'),
	]

	operations = [
		migrations.AlterField(
			model_name='notificacion',
			name='tipo',
			field=models.CharField(
				choices=[
					('COTIZACION', 'Cotizacion'),
					('PEDIDO', 'Pedido'),
					('NOTA_AJUSTE', 'Nota de ajuste'),
					('CLIENTE', 'Solicitud de cliente'),
				],
				max_length=20,
			),
		),
		migrations.CreateModel(
			name='WorkspaceCustomerRequestAlertReadState',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('last_opened_at', models.DateTimeField(blank=True, null=True)),
				('updated_at', models.DateTimeField(auto_now=True)),
				(
					'user',
					models.OneToOneField(
						on_delete=django.db.models.deletion.CASCADE,
						related_name='customer_request_alert_read_state',
						to=settings.AUTH_USER_MODEL,
					),
				),
			],
			options={
				'db_table': 'notificaciones_customer_request_alert_read_state',
			},
		),
	]
