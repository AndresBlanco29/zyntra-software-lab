from django.utils.translation import gettext_lazy as _


ROLE_STYLES = {
    'cliente': {'color_start': '#dbeafe', 'color_end': '#dbeafe', 'text_color': '#1e3a8a'},
    'vendedor': {'color_start': '#dcfce7', 'color_end': '#dcfce7', 'text_color': '#166534'},
    'backoffice': {'color_start': '#e0e7ff', 'color_end': '#e0e7ff', 'text_color': '#312e81'},
    'seleccionador': {'color_start': '#cffafe', 'color_end': '#cffafe', 'text_color': '#155e75'},
    'driver': {'color_start': '#ffedd5', 'color_end': '#ffedd5', 'text_color': '#9a3412'},
}

ROLE_LABELS = {
    'cliente': _('Customer'),
    'vendedor': _('Sales rep'),
    'backoffice': _('Backoffice'),
    'seleccionador': _('Picker'),
    'driver': _('Driver'),
}


def _role_style(role):
    return ROLE_STYLES.get(role, ROLE_STYLES['backoffice'])


def _safe_related(obj, attr_name):
    """Safely read reverse OneToOne / FK relations that may raise DoesNotExist."""
    if obj is None:
        return None
    try:
        return getattr(obj, attr_name)
    except Exception:
        return None


def _split_badge(*, sender_role, receiver_role, label=None):
    sender_style = _role_style(sender_role)
    receiver_style = _role_style(receiver_role)
    return {
        'kind': 'split',
        'sender_role': sender_role,
        'receiver_role': receiver_role,
        'label': label or f'{ROLE_LABELS[sender_role]} → {ROLE_LABELS[receiver_role]}',
        'color_start': sender_style['color_start'],
        'color_end': receiver_style['color_end'],
        'text_color': sender_style['text_color'],
    }


def _solid_badge(*, role, label=None):
    style = _role_style(role)
    return {
        'kind': 'solid',
        'sender_role': role,
        'receiver_role': role,
        'label': label or ROLE_LABELS[role],
        'color_start': style['color_start'],
        'color_end': style['color_end'],
        'text_color': style['text_color'],
    }


def build_quote_workflow_badge(cotizacion):
    estado = getattr(cotizacion, 'estado', '') or ''

    if estado == 'ENVIADA':
        if getattr(cotizacion, 'vendedor_id', None):
            return _split_badge(sender_role='vendedor', receiver_role='backoffice', label=_('Awaiting BackOffice review'))
        return _split_badge(sender_role='cliente', receiver_role='backoffice', label=_('Awaiting BackOffice review'))

    if estado == 'LISTA_PARA_CONFIRMACION':
        return _split_badge(sender_role='backoffice', receiver_role='cliente', label=_('Awaiting client confirmation'))

    if estado == 'CONFIRMADA_CLIENTE':
        return _split_badge(sender_role='cliente', receiver_role='backoffice', label=_('Ready to convert to order'))

    if estado == 'CANCELADA_CLIENTE':
        return _solid_badge(role='cliente', label=_('Cancelled by client'))

    if estado == 'APROBADA':
        return _solid_badge(role='backoffice', label=_('Approved'))

    if estado == 'RECHAZADA':
        return _solid_badge(role='backoffice', label=_('Rejected'))

    return _solid_badge(role='backoffice')


def build_order_workflow_badge(pedido):
    estado = getattr(pedido, 'estado', '') or ''
    origen = getattr(pedido, 'origen', '') or ''
    invoice = _safe_related(pedido, 'invoice')
    delivery = _safe_related(invoice, 'delivery')
    selector = getattr(pedido, 'seleccionador', None)
    selector_label = (selector.get_full_name() or selector.username) if selector else ''

    if estado == 'CANCELADO':
        return _solid_badge(role='backoffice', label=_('Cancelled'))

    if invoice and invoice.estado == 'ANULADA':
        return _solid_badge(role='backoffice', label=_('Invoice voided'))

    if delivery and delivery.estado in {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}:
        return _solid_badge(role='driver', label=_('Delivery completed'))
    if delivery and delivery.estado == 'EN_RUTA':
        driver = delivery.driver
        driver_label = (driver.get_full_name() or driver.username) if driver else ''
        return _solid_badge(role='driver', label=driver_label or _('On the way'))

    if estado == 'INVOICE_GENERADA' or (invoice and invoice.estado == 'GENERADA'):
        if delivery and getattr(delivery, 'is_customer_pickup', False):
            return _solid_badge(role='cliente', label=_('Waiting for customer pickup'))
        if delivery and delivery.driver_id:
            driver = delivery.driver
            driver_label = (driver.get_full_name() or driver.username) if driver else ''
            return _solid_badge(role='driver', label=driver_label or _('Assigned to driver'))
        return _split_badge(sender_role='backoffice', receiver_role='driver', label=_('Awaiting driver assignment'))

    if estado == 'VERIFICADO_AJUSTADO':
        return _solid_badge(role='backoffice', label=_('Ready to generate invoice'))

    if estado == 'PARA_VERIFICAR':
        if selector_label:
            return _solid_badge(role='seleccionador', label=selector_label)
        return _split_badge(sender_role='backoffice', receiver_role='seleccionador', label=_('With picker'))

    if estado == 'LISTO_PARA_PICKING':
        return _solid_badge(role='backoffice', label=_('Ready to send to picking'))

    if estado == 'EN_GESTION':
        return _solid_badge(role='backoffice', label=_('Being managed by BackOffice'))

    if origen == 'VENDEDOR':
        return _split_badge(sender_role='vendedor', receiver_role='backoffice', label=_('Sales rep → BackOffice'))

    if origen == 'CLIENTE':
        return _split_badge(sender_role='cliente', receiver_role='backoffice', label=_('Customer → BackOffice'))

    return _solid_badge(role='backoffice', label=_('Awaiting BackOffice review'))


def build_delivery_workflow_badge(delivery):
    estado = getattr(delivery, 'estado', '') or ''

    if estado in {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}:
        return _solid_badge(role='driver', label=_('Delivered'))

    if estado in {'ASIGNADA', 'EN_RUTA'}:
        return _split_badge(sender_role='backoffice', receiver_role='driver')

    return _solid_badge(role='backoffice')
