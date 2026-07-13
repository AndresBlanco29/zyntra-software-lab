from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def copy_fk_assignments_to_through_table(apps, schema_editor):
	Cliente = apps.get_model('clientes', 'Cliente')
	ClienteVendedorAsignacion = apps.get_model('clientes', 'ClienteVendedorAsignacion')
	rows = []
	now = timezone.now()
	for cliente in Cliente.objects.exclude(vendedor_asignado_id=None).iterator():
		rows.append(
			ClienteVendedorAsignacion(
				cliente_id=cliente.id,
				vendedor_id=cliente.vendedor_asignado_id,
				asignado_por_id=cliente.vendedor_asignado_por_id,
				asignado_en=cliente.vendedor_asignado_en or now,
			)
		)
		if len(rows) >= 500:
			ClienteVendedorAsignacion.objects.bulk_create(rows, ignore_conflicts=True)
			rows = []
	if rows:
		ClienteVendedorAsignacion.objects.bulk_create(rows, ignore_conflicts=True)


def noop_reverse(apps, schema_editor):
	pass


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('clientes', '0013_alter_cliente_terminos_pago_ach_net7'),
	]

	operations = [
		migrations.CreateModel(
			name='ClienteVendedorAsignacion',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('asignado_en', models.DateTimeField()),
				(
					'asignado_por',
					models.ForeignKey(
						blank=True,
						null=True,
						on_delete=django.db.models.deletion.SET_NULL,
						related_name='asignaciones_clientes_realizadas',
						to=settings.AUTH_USER_MODEL,
					),
				),
				(
					'cliente',
					models.ForeignKey(
						on_delete=django.db.models.deletion.CASCADE,
						related_name='asignaciones_vendedores',
						to='clientes.cliente',
					),
				),
				(
					'vendedor',
					models.ForeignKey(
						limit_choices_to={'role': 'vendedor'},
						on_delete=django.db.models.deletion.CASCADE,
						related_name='asignaciones_clientes',
						to=settings.AUTH_USER_MODEL,
					),
				),
			],
			options={
				'verbose_name': 'Customer vendor assignment',
				'verbose_name_plural': 'Customer vendor assignments',
			},
		),
		migrations.AddConstraint(
			model_name='clientevendedorasignacion',
			constraint=models.UniqueConstraint(
				fields=('cliente', 'vendedor'),
				name='uniq_cliente_vendedor_asignacion',
			),
		),
		migrations.RunPython(copy_fk_assignments_to_through_table, noop_reverse),
		migrations.AlterField(
			model_name='clientevendedorasignacion',
			name='asignado_en',
			field=models.DateTimeField(auto_now_add=True),
		),
	]
