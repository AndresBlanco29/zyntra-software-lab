from django.utils import timezone

from config.clientes.models import Cliente, ClienteVendedorAsignacion
from config.usuarios.models import Usuario


def filter_clientes_for_vendedor(queryset, user):
	if getattr(user, 'role', '') == 'vendedor':
		return queryset.filter(asignaciones_vendedores__vendedor=user).distinct()
	return queryset


def ensure_cliente_assigned_to_vendedor(*, cliente, vendedor, assigned_by=None):
	"""Attach customer to vendor without removing other vendor assignments."""
	now = timezone.now()
	assignment, created = ClienteVendedorAsignacion.objects.get_or_create(
		cliente=cliente,
		vendedor=vendedor,
		defaults={
			'asignado_por': assigned_by,
		},
	)
	if created and assigned_by is not None and assignment.asignado_por_id != getattr(assigned_by, 'id', None):
		assignment.asignado_por = assigned_by
		assignment.save(update_fields=['asignado_por'])

	# Keep legacy single FK filled only when empty, so other vendors are not overwritten.
	if cliente.vendedor_asignado_id is None:
		cliente.vendedor_asignado = vendedor
		cliente.vendedor_asignado_en = now
		cliente.vendedor_asignado_por = assigned_by
		cliente.save(update_fields=['vendedor_asignado', 'vendedor_asignado_en', 'vendedor_asignado_por'])

	return created


def sync_vendedor_cliente_assignments(*, vendedor, selected_cliente_ids, assigned_by):
	selected_ids = {int(value) for value in selected_cliente_ids if str(value).isdigit()}
	currently_assigned_ids = set(
		ClienteVendedorAsignacion.objects.filter(vendedor=vendedor).values_list('cliente_id', flat=True)
	)
	to_assign_ids = selected_ids - currently_assigned_ids
	to_unassign_ids = currently_assigned_ids - selected_ids
	now = timezone.now()

	unassigned_count = 0
	if to_unassign_ids:
		unassigned_count, _ = ClienteVendedorAsignacion.objects.filter(
			vendedor=vendedor,
			cliente_id__in=to_unassign_ids,
		).delete()
		# Clear legacy FK only when it pointed at this vendor and no other assignment remains.
		for cliente in Cliente.objects.filter(id__in=to_unassign_ids, vendedor_asignado=vendedor):
			replacement = (
				ClienteVendedorAsignacion.objects.filter(cliente=cliente)
				.select_related('vendedor')
				.order_by('asignado_en')
				.first()
			)
			if replacement is None:
				cliente.vendedor_asignado = None
				cliente.vendedor_asignado_en = None
				cliente.vendedor_asignado_por = None
			else:
				cliente.vendedor_asignado = replacement.vendedor
				cliente.vendedor_asignado_en = replacement.asignado_en
				cliente.vendedor_asignado_por = replacement.asignado_por
			cliente.save(update_fields=['vendedor_asignado', 'vendedor_asignado_en', 'vendedor_asignado_por'])

	assigned_count = 0
	if to_assign_ids:
		approved_clients = list(
			Cliente.objects.filter(id__in=to_assign_ids, aprobado=True)
		)
		ClienteVendedorAsignacion.objects.bulk_create(
			[
				ClienteVendedorAsignacion(
					cliente=cliente,
					vendedor=vendedor,
					asignado_por=assigned_by,
				)
				for cliente in approved_clients
			],
			ignore_conflicts=True,
		)
		assigned_count = len(approved_clients)
		# Fill empty legacy FK without stealing an existing primary vendor.
		Cliente.objects.filter(
			id__in=[cliente.id for cliente in approved_clients],
			vendedor_asignado__isnull=True,
		).update(
			vendedor_asignado=vendedor,
			vendedor_asignado_en=now,
			vendedor_asignado_por=assigned_by,
		)

	return {
		'assigned_count': assigned_count,
		'unassigned_count': unassigned_count,
		'selected_count': len(selected_ids),
	}


def assign_all_approved_clientes_to_vendedor(*, vendedor, assigned_by):
	"""Add every approved customer to this vendor. Does not remove other vendors' lists."""
	now = timezone.now()
	approved_ids = list(Cliente.objects.filter(aprobado=True).values_list('id', flat=True))
	existing_ids = set(
		ClienteVendedorAsignacion.objects.filter(
			vendedor=vendedor,
			cliente_id__in=approved_ids,
		).values_list('cliente_id', flat=True)
	)
	missing_ids = [cliente_id for cliente_id in approved_ids if cliente_id not in existing_ids]
	if missing_ids:
		ClienteVendedorAsignacion.objects.bulk_create(
			[
				ClienteVendedorAsignacion(
					cliente_id=cliente_id,
					vendedor=vendedor,
					asignado_por=assigned_by,
				)
				for cliente_id in missing_ids
			],
			ignore_conflicts=True,
		)
		Cliente.objects.filter(id__in=missing_ids, vendedor_asignado__isnull=True).update(
			vendedor_asignado=vendedor,
			vendedor_asignado_en=now,
			vendedor_asignado_por=assigned_by,
		)
	return len(approved_ids)


def get_active_vendedores_queryset():
	return Usuario.objects.filter(role='vendedor', is_active=True).order_by('first_name', 'last_name', 'username')
