from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('pedidos', '0005_picking_verification_flow'),
	]

	operations = [
		migrations.AlterField(
			model_name='pedido',
			name='estado',
			field=models.CharField(choices=[('RECIBIDO', 'Recibido'), ('EN_GESTION', 'En gestion'), ('LISTO_PARA_PICKING', 'Listo para picking'), ('PARA_VERIFICAR', 'Para verificar'), ('VERIFICADO_AJUSTADO', 'Verificado y ajustado'), ('INVOICE_GENERADA', 'Invoice generada'), ('DESPACHADO', 'Despachado'), ('CANCELADO', 'Cancelado')], default='RECIBIDO', max_length=30),
		),
	]