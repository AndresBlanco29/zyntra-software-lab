from django.urls import reverse
from django.utils.translation import gettext as _


def _is_internal_panel_user(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(user, 'role', None) != 'cliente'


def _append_alert(items, *, label, detail, url, count, priority='medium', kind='general'):
    count = int(count or 0)
    if count <= 0:
        return
    items.append({
        'label': label,
        'detail': detail,
        'url': url,
        'count': count,
        'priority': priority,
        'kind': kind,
    })


def get_urgent_workspace_alerts(user):
    if not _is_internal_panel_user(user):
        return None
    if not (
        user.has_internal_permission('backoffice.orders.view')
        or user.has_internal_permission('backoffice.quotes.view')
    ):
        return None

    from config.cotizaciones.models import Cotizacion
    from config.pedidos.dispatch_orders import (
        PEDIDO_IN_PROGRESS_STATUSES,
        PEDIDO_PENDING_STATUSES,
        QUOTE_PENDING_STATUSES,
    )
    from config.pedidos.models import Pedido

    orders_url = reverse('backoffice_pedidos')
    summary_items = []
    open_quotes = Cotizacion.objects.select_related('cliente').filter(
        pedido_generado__isnull=True,
    ).order_by('-fecha')
    pedidos = Pedido.objects.select_related('cliente').order_by('-creada_en')

    pending_requests = open_quotes.filter(estado__in=QUOTE_PENDING_STATUSES).count()
    _append_alert(
        summary_items,
        label=_('Pending review'),
        detail=_('New customer order requests waiting for BackOffice'),
        url=orders_url,
        count=pending_requests,
        priority='high',
        kind='orders-pending-review',
    )

    awaiting_customer = open_quotes.filter(estado='LISTA_PARA_CONFIRMACION').count()
    _append_alert(
        summary_items,
        label=_('Waiting for customer'),
        detail=_('Orders sent to the customer that are still open'),
        url=orders_url,
        count=awaiting_customer,
        priority='medium',
        kind='orders-awaiting-customer',
    )

    ready_for_dispatch = pedidos.filter(estado__in=PEDIDO_PENDING_STATUSES).count()
    _append_alert(
        summary_items,
        label=_('Ready to dispatch'),
        detail=_('Confirmed orders waiting for picking or dispatch'),
        url=orders_url,
        count=ready_for_dispatch,
        priority='high',
        kind='orders-ready-dispatch',
    )

    in_progress = pedidos.filter(estado__in=PEDIDO_IN_PROGRESS_STATUSES).count()
    _append_alert(
        summary_items,
        label=_('In progress'),
        detail=_('Orders currently being managed, verified, or invoiced'),
        url=f'{orders_url}?view=in-progress',
        count=in_progress,
        priority='medium',
        kind='orders-in-progress',
    )

    recent_items = []
    for cotizacion in open_quotes.filter(estado__in=QUOTE_PENDING_STATUSES)[:4]:
        recent_items.append({
            'title': _('Order request #%(id)s · %(customer)s') % {
                'id': cotizacion.id,
                'customer': cotizacion.cliente.nombre_empresa,
            },
            'message': str(cotizacion.get_estado_display()),
            'url': reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]),
            'sort_date': cotizacion.fecha,
        })
    for pedido in pedidos.filter(estado__in=PEDIDO_PENDING_STATUSES | PEDIDO_IN_PROGRESS_STATUSES)[:4]:
        recent_items.append({
            'title': _('Order #%(id)s · %(customer)s') % {
                'id': pedido.id,
                'customer': pedido.cliente.nombre_empresa,
            },
            'message': pedido.get_estado_display(),
            'url': reverse('backoffice_pedido_detalle', args=[pedido.id]),
            'sort_date': pedido.creada_en,
        })
    recent_items.sort(key=lambda item: item['sort_date'], reverse=True)
    recent_items = recent_items[:8]

    total_count = pending_requests + awaiting_customer + ready_for_dispatch + in_progress

    return {
        'total_count': total_count,
        'summary_items': summary_items,
        'recent_items': recent_items,
        'orders_url': orders_url,
    }
