from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _


def _is_internal_panel_user(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(user, 'role', None) != 'cliente'


def _append_alert(items, *, label, detail, url, count, unread_count=0, priority='medium', kind='general'):
    count = int(count or 0)
    if count <= 0:
        return
    items.append({
        'label': label,
        'detail': detail,
        'url': url,
        'count': count,
        'unread_count': int(unread_count or 0),
        'priority': priority,
        'kind': kind,
    })


def _get_dispatch_alert_last_seen_at(user):
    from config.notificaciones.models import WorkspaceDispatchAlertReadState

    state = WorkspaceDispatchAlertReadState.objects.filter(user=user).only('last_opened_at').first()
    return state.last_opened_at if state else None


def _is_dispatch_alert_unread(*, activity_at, last_seen_at):
    if activity_at is None:
        return False
    if last_seen_at is None:
        return True
    return activity_at > last_seen_at


def _quote_activity_at(cotizacion):
    return cotizacion.fecha


def _pedido_activity_at(pedido):
    return pedido.actualizada_en or pedido.creada_en


def _count_unread_queryset(queryset, *, activity_getter, last_seen_at):
    if last_seen_at is None:
        return queryset.count()
    return sum(1 for obj in queryset if _is_dispatch_alert_unread(activity_at=activity_getter(obj), last_seen_at=last_seen_at))


def mark_dispatch_alerts_seen(user):
    from config.notificaciones.models import Notificacion, WorkspaceDispatchAlertReadState

    now = timezone.now()
    WorkspaceDispatchAlertReadState.objects.update_or_create(
        user=user,
        defaults={'last_opened_at': now},
    )
    Notificacion.objects.filter(
        tipo__in=('PEDIDO', 'COTIZACION'),
        leida=False,
    ).filter(
        Q(usuario=user) | Q(usuario__isnull=True),
    ).update(leida=True)
    return now


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
        PEDIDO_PENDING_STATUSES,
        QUOTE_PENDING_STATUSES,
    )
    from config.pedidos.models import Pedido

    orders_url = reverse('backoffice_pedidos')
    summary_items = []
    last_seen_at = _get_dispatch_alert_last_seen_at(user)
    open_quotes = Cotizacion.objects.select_related('cliente').filter(
        pedido_generado__isnull=True,
    ).order_by('-fecha')
    pedidos = Pedido.objects.select_related('cliente').order_by('-creada_en')

    pending_requests_qs = open_quotes.filter(estado__in=QUOTE_PENDING_STATUSES)
    pending_requests = pending_requests_qs.count()
    unread_pending_requests = _count_unread_queryset(
        pending_requests_qs,
        activity_getter=_quote_activity_at,
        last_seen_at=last_seen_at,
    )
    _append_alert(
        summary_items,
        label=_('Pending review'),
        detail=_('New customer order requests waiting for BackOffice'),
        url=orders_url,
        count=pending_requests,
        unread_count=unread_pending_requests,
        priority='high',
        kind='orders-pending-review',
    )

    awaiting_customer_qs = open_quotes.filter(estado='LISTA_PARA_CONFIRMACION')
    awaiting_customer = awaiting_customer_qs.count()
    unread_awaiting_customer = _count_unread_queryset(
        awaiting_customer_qs,
        activity_getter=_quote_activity_at,
        last_seen_at=last_seen_at,
    )
    _append_alert(
        summary_items,
        label=_('Waiting for customer'),
        detail=_('Orders sent to the customer that are still open'),
        url=orders_url,
        count=awaiting_customer,
        unread_count=unread_awaiting_customer,
        priority='medium',
        kind='orders-awaiting-customer',
    )

    ready_for_dispatch_qs = pedidos.filter(estado__in=PEDIDO_PENDING_STATUSES)
    ready_for_dispatch = ready_for_dispatch_qs.count()
    unread_ready_for_dispatch = _count_unread_queryset(
        ready_for_dispatch_qs,
        activity_getter=_pedido_activity_at,
        last_seen_at=last_seen_at,
    )
    _append_alert(
        summary_items,
        label=_('Ready to dispatch'),
        detail=_('Confirmed orders waiting for picking or dispatch'),
        url=orders_url,
        count=ready_for_dispatch,
        unread_count=unread_ready_for_dispatch,
        priority='high',
        kind='orders-ready-dispatch',
    )

    recent_items = []
    for cotizacion in pending_requests_qs[:8]:
        recent_items.append({
            'title': _('Order request #%(id)s · %(customer)s') % {
                'id': cotizacion.id,
                'customer': cotizacion.cliente.nombre_empresa,
            },
            'message': str(cotizacion.get_estado_display()),
            'url': reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]),
            'sort_date': cotizacion.fecha,
            'is_unread': _is_dispatch_alert_unread(activity_at=_quote_activity_at(cotizacion), last_seen_at=last_seen_at),
        })
    for pedido in ready_for_dispatch_qs[:8]:
        recent_items.append({
            'title': _('Order #%(id)s · %(customer)s') % {
                'id': pedido.id,
                'customer': pedido.cliente.nombre_empresa,
            },
            'message': pedido.get_estado_display(),
            'url': reverse('backoffice_pedido_detalle', args=[pedido.id]),
            'sort_date': pedido.creada_en,
            'is_unread': _is_dispatch_alert_unread(activity_at=_pedido_activity_at(pedido), last_seen_at=last_seen_at),
        })
    recent_items.sort(key=lambda item: item['sort_date'], reverse=True)
    recent_items = recent_items[:8]

    total_count = unread_pending_requests + unread_awaiting_customer + unread_ready_for_dispatch

    return {
        'total_count': total_count,
        'summary_items': summary_items,
        'recent_items': recent_items,
        'orders_url': orders_url,
        'mark_seen_url': reverse('mark_dispatch_alerts_seen'),
    }
