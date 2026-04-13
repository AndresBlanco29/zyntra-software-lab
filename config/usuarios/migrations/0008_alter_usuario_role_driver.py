from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('usuarios', '0007_alter_usuario_role_selector'),
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
					('driver', 'Driver'),
					('cliente', 'Cliente'),
				],
				default='cliente',
				max_length=20,
			),
		),
	]