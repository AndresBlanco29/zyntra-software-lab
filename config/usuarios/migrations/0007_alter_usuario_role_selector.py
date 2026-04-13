from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('usuarios', '0006_usuario_permission_overrides'),
	]

	operations = [
		migrations.AlterField(
			model_name='usuario',
			name='role',
			field=models.CharField(
				choices=[
					('admin', 'Administrador'),
					('vendedor', 'Vendedor'),
					('backoffice', 'BackOffice'),
					('seleccionador', 'Seleccionador'),
					('cliente', 'Cliente'),
				],
				default='cliente',
				max_length=20,
			),
		),
	]