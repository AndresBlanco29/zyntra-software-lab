from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		('clientes', '0009_cliente_terminos_pago'),
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('pedidos', '0010_pedidoitem_line_discount'),
	]

	operations = [
		migrations.AddField(
			model_name='cliente',
			name='credit_limit',
			field=models.DecimalField(
				blank=True,
				decimal_places=2,
				help_text='Maximum total due balance allowed for this customer. Leave empty for no limit.',
				max_digits=12,
				null=True,
			),
		),
		migrations.CreateModel(
			name='ClienteCreditoLimiteAlerta',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('monto_adeudado', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('monto_operacion', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('limite_credito', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('exceso', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				(
					'estado',
					models.CharField(
						choices=[('PENDIENTE', 'Pending review'), ('LIBERADO', 'Released'), ('BLOQUEADO', 'Blocked')],
						db_index=True,
						default='PENDIENTE',
						max_length=20,
					),
				),
				('creado_en', models.DateTimeField(auto_now_add=True)),
				('resuelto_en', models.DateTimeField(blank=True, null=True)),
				(
					'cliente',
					models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alertas_limite_credito', to='clientes.cliente'),
				),
				(
					'pedido',
					models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='alertas_limite_credito', to='pedidos.pedido'),
				),
				(
					'resuelto_por',
					models.ForeignKey(
						blank=True,
						null=True,
						on_delete=django.db.models.deletion.SET_NULL,
						related_name='alertas_limite_credito_resueltas',
						to=settings.AUTH_USER_MODEL,
					),
				),
			],
			options={
				'ordering': ('-creado_en',),
			},
		),
	]
