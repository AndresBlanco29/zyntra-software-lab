from django.db import migrations, models
import django.db.models.deletion


DEFAULT_TIPOS_CLIENTE = (
	('supermercados', 'Supermarkets', 1),
	('distribuidores', 'Distributors', 2),
)


def seed_tipos_cliente(apps, schema_editor):
	TipoCliente = apps.get_model('clientes', 'TipoCliente')
	for codigo, nombre, orden in DEFAULT_TIPOS_CLIENTE:
		TipoCliente.objects.get_or_create(
			codigo=codigo,
			defaults={'nombre': nombre, 'orden': orden, 'activo': True},
		)


def noop_reverse(apps, schema_editor):
	pass


class Migration(migrations.Migration):

	dependencies = [
		('clientes', '0014_clientevendedorasignacion'),
	]

	operations = [
		migrations.CreateModel(
			name='TipoCliente',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('codigo', models.SlugField(help_text='Stable internal identifier, e.g. "supermercados".', max_length=50, unique=True, verbose_name='Code')),
				('nombre', models.CharField(max_length=100, verbose_name='Name')),
				('nombre_en', models.CharField(blank=True, max_length=100, verbose_name='Name (English)')),
				('activo', models.BooleanField(default=True, verbose_name='Active')),
				('orden', models.PositiveSmallIntegerField(default=0, verbose_name='Display order')),
			],
			options={
				'verbose_name': 'Customer type',
				'verbose_name_plural': 'Customer types',
				'ordering': ['orden', 'nombre'],
			},
		),
		migrations.AddField(
			model_name='cliente',
			name='tipo_cliente',
			field=models.ForeignKey(
				blank=True,
				help_text='Segment used to target promotions (e.g. Supermarkets, Distributors).',
				null=True,
				on_delete=django.db.models.deletion.SET_NULL,
				related_name='clientes',
				to='clientes.tipocliente',
				verbose_name='Customer type',
			),
		),
		migrations.RunPython(seed_tipos_cliente, noop_reverse),
	]
