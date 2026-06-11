from django.utils import timezone

from config.clientes.models import Cliente
from config.usuarios.models import Usuario


def filter_clientes_for_vendedor(queryset, user):
    if getattr(user, 'role', '') == 'vendedor':
        return queryset.filter(vendedor_asignado=user)
    return queryset


def sync_vendedor_cliente_assignments(*, vendedor, selected_cliente_ids, assigned_by):
    selected_ids = {int(value) for value in selected_cliente_ids if str(value).isdigit()}
    currently_assigned_ids = set(
        Cliente.objects.filter(vendedor_asignado=vendedor).values_list('id', flat=True)
    )
    to_assign_ids = selected_ids - currently_assigned_ids
    to_unassign_ids = currently_assigned_ids - selected_ids
    now = timezone.now()

    if to_unassign_ids:
        Cliente.objects.filter(id__in=to_unassign_ids, vendedor_asignado=vendedor).update(
            vendedor_asignado=None,
            vendedor_asignado_en=None,
            vendedor_asignado_por=None,
        )

    assigned_count = 0
    if to_assign_ids:
        assigned_count = Cliente.objects.filter(
            id__in=to_assign_ids,
            aprobado=True,
        ).update(
            vendedor_asignado=vendedor,
            vendedor_asignado_en=now,
            vendedor_asignado_por=assigned_by,
        )

    return {
        'assigned_count': assigned_count,
        'unassigned_count': len(to_unassign_ids),
        'selected_count': len(selected_ids),
    }


def assign_all_approved_clientes_to_vendedor(*, vendedor, assigned_by):
    now = timezone.now()
    count = Cliente.objects.filter(aprobado=True).update(
        vendedor_asignado=vendedor,
        vendedor_asignado_en=now,
        vendedor_asignado_por=assigned_by,
    )
    return count


def get_active_vendedores_queryset():
    return Usuario.objects.filter(role='vendedor', is_active=True).order_by('first_name', 'last_name', 'username')
