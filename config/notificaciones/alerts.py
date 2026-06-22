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

    summary_items = []
    recent_notifications = []
    total_count = 0

    if user.has_internal_permission('backoffice.orders.view'):
        from config.pedidos.models import Pedido

        received_orders = Pedido.objects.filter(estado='RECIBIDO').count()
        _append_alert(
            summary_items,
            label=_('New customer orders'),
            detail=_('Orders received and waiting for backoffice review'),
            url=reverse('backoffice_pedidos'),
            count=received_orders,
            priority='high',
            kind='orders-received',
        )

        ready_for_picking = Pedido.objects.filter(estado='LISTO_PARA_PICKING').count()
        _append_alert(
            summary_items,
            label=_('Orders ready for picking'),
            detail=_('Verified orders waiting in the warehouse queue'),
            url=reverse('backoffice_pedidos'),
            count=ready_for_picking,
            priority='high',
            kind='picking',
        )

        in_progress_orders = Pedido.objects.filter(estado='EN_GESTION').count()
        _append_alert(
            summary_items,
            label=_('Orders in progress'),
            detail=_('Sales orders currently being processed'),
            url=f"{reverse('backoffice_pedidos')}?view=in-progress",
            count=in_progress_orders,
            priority='medium',
            kind='orders-progress',
        )

    if user.has_internal_permission('backoffice.quotes.view'):
        from config.cotizaciones.models import Cotizacion

        pending_quotes = Cotizacion.objects.filter(estado__in=['ENVIADA', 'LISTA_PARA_CONFIRMACION']).count()
        _append_alert(
            summary_items,
            label=_('Pending quotes'),
            detail=_('Customer quote requests waiting for review'),
            url=reverse('backoffice_cotizaciones'),
            count=pending_quotes,
            priority='high',
            kind='quotes',
        )

    if user.has_internal_permission('backoffice.dashboard.view'):
        from config.facturacion.models import NotaAjuste
        from config.inventario.models import StockPresentacion
        from config.notificaciones.models import Notificacion

        pending_notes = NotaAjuste.objects.filter(estado='BORRADOR').count()
        _append_alert(
            summary_items,
            label=_('Adjustment notes pending approval'),
            detail=_('Credit or debit notes waiting for review'),
            url=reverse('backoffice_adjustment_notes_list'),
            count=pending_notes,
            priority='high',
            kind='notes',
        )

        out_of_stock = StockPresentacion.objects.filter(stock_disponible__lte=0).count()
        _append_alert(
            summary_items,
            label=_('Products without stock'),
            detail=_('Presentations with zero available inventory'),
            url=reverse('backoffice_inventory_list'),
            count=out_of_stock,
            priority='medium',
            kind='inventory',
        )

        unread_notifications = Notificacion.objects.filter(leida=False).order_by('-creada_en')
        unread_count = unread_notifications.count()
        recent_notifications = list(unread_notifications[:8])
        if unread_count:
            _append_alert(
                summary_items,
                label=_('Unread system alerts'),
                detail=_('Orders, quotes, and adjustment messages'),
                url=f"{reverse('backoffice_dashboard')}#system-notifications",
                count=unread_count,
                priority='high',
                kind='notifications',
            )

    for item in summary_items:
        total_count += item['count']

    return {
        'total_count': total_count,
        'summary_items': summary_items,
        'recent_notifications': recent_notifications,
        'dashboard_url': reverse('backoffice_dashboard'),
    }
