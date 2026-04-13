from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_requested_quantity(apps, schema_editor):
	PedidoItem = apps.get_model('pedidos', 'PedidoItem')
	for item in PedidoItem.objects.all().iterator():
		item.cantidad_solicitada = item.cantidad
		item.save(update_fields=['cantidad_solicitada'])


class Migration(migrations.Migration):

	dependencies = [
		('pedidos', '0004_pedido_acepta_terminos_en'),
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
	]

	operations = [
		migrations.AddField(
			model_name='pedido',
			name='nota_seleccionador',
			field=models.TextField(blank=True),
		),
		migrations.AddField(
			model_name='pedido',
			name='nota_seleccionador_resuelta',
			field=models.BooleanField(default=False),
		),
		migrations.AddField(
			model_name='pedido',
			name='picking_asignado_en',
			field=models.DateTimeField(blank=True, null=True),
		),
		migrations.AddField(
			model_name='pedido',
			name='picking_bloqueado',
			field=models.BooleanField(default=False),
		),
		migrations.AddField(
			model_name='pedido',
			name='picking_verificado_en',
			field=models.DateTimeField(blank=True, null=True),
		),
		migrations.AddField(
			model_name='pedido',
			name='seleccionador',
			field=models.ForeignKey(blank=True, limit_choices_to={'role': 'seleccionador'}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='picking_tickets_asignados', to=settings.AUTH_USER_MODEL),
		),
		migrations.AddField(
			model_name='pedidoitem',
			name='cantidad_solicitada',
			field=models.PositiveIntegerField(default=1),
		),
		migrations.AlterField(
			model_name='pedido',
			name='estado',
			field=models.CharField(choices=[('RECIBIDO', 'Recibido'), ('EN_GESTION', 'En gestion'), ('LISTO_PARA_PICKING', 'Listo para picking'), ('PARA_VERIFICAR', 'Para verificar'), ('VERIFICADO_AJUSTADO', 'Verificado y ajustado'), ('DESPACHADO', 'Despachado'), ('CANCELADO', 'Cancelado')], default='RECIBIDO', max_length=30),
		),
		migrations.RunPython(copy_requested_quantity, migrations.RunPython.noop),
	]