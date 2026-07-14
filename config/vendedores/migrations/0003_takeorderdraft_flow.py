from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('vendedores', '0002_takeorderdraft_nota'),
	]

	operations = [
		migrations.AddField(
			model_name='takeorderdraft',
			name='flow',
			field=models.CharField(
				choices=[('order', 'Order'), ('quote', 'Quote')],
				default='order',
				max_length=16,
			),
		),
		migrations.RemoveConstraint(
			model_name='takeorderdraft',
			name='vendedores_takeorderdraft_vendedor_cliente_uniq',
		),
		migrations.AddConstraint(
			model_name='takeorderdraft',
			constraint=models.UniqueConstraint(
				fields=('vendedor', 'cliente', 'flow'),
				name='vendedores_takeorderdraft_vendedor_cliente_flow_uniq',
			),
		),
	]
