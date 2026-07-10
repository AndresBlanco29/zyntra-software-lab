from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		('pedidos', '0012_restore_pedido_item_requested_quantities'),
	]

	operations = [
		migrations.AddField(
			model_name='pedido',
			name='pedido_raiz',
			field=models.ForeignKey(
				blank=True,
				null=True,
				on_delete=django.db.models.deletion.CASCADE,
				related_name='parciales',
				to='pedidos.pedido',
			),
		),
		migrations.AddField(
			model_name='pedido',
			name='indice_parcial',
			field=models.PositiveIntegerField(blank=True, null=True),
		),
		migrations.AddField(
			model_name='pedidoitem',
			name='item_origen',
			field=models.ForeignKey(
				blank=True,
				null=True,
				on_delete=django.db.models.deletion.SET_NULL,
				related_name='items_parciales',
				to='pedidos.pedidoitem',
			),
		),
		migrations.AddConstraint(
			model_name='pedido',
			constraint=models.UniqueConstraint(
				fields=('pedido_raiz', 'indice_parcial'),
				name='pedidos_pedido_raiz_indice_parcial_uniq',
			),
		),
	]
