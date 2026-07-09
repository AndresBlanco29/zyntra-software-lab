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


def build_order_workflow_badge(pedido):
    estado = getattr(pedido, 'estado', '') or ''
    origen = getattr(pedido, 'origen', '') or ''
    invoice = getattr(pedido, 'invoice', None)
    delivery = getattr(invoice, 'delivery', None) if invoice else None

    if estado == 'CANCELADO':
        return _solid_badge(role='backoffice', label=_('Cancelled'))

    if delivery and delivery.estado in {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}:
        return _solid_badge(role='driver', label=_('Delivered'))

    if delivery and delivery.estado == 'EN_RUTA':
        return _split_badge(sender_role='driver', receiver_role='cliente', label=_('Out for delivery'))

    if estado == 'INVOICE_GENERADA' or (invoice and invoice.estado == 'GENERADA'):
        if delivery and getattr(delivery, 'is_customer_pickup', False):
            return _solid_badge(role='backoffice', label=_('Customer pick up'))
        if delivery and delivery.driver_id:
            driver = delivery.driver
            driver_label = (driver.get_full_name() or driver.username) if driver else ''
            return _solid_badge(role='driver', label=driver_label or _('With driver'))
        return _split_badge(sender_role='backoffice', receiver_role='driver', label=_('With driver'))

    if estado == 'VERIFICADO_AJUSTADO':
        return _solid_badge(role='backoffice', label=_('Ready for invoice'))

    if estado in {'PARA_VERIFICAR', 'LISTO_PARA_PICKING'}:
        return _split_badge(sender_role='backoffice', receiver_role='seleccionador', label=_('Picking'))

    if origen == 'VENDEDOR':
        return _split_badge(sender_role='vendedor', receiver_role='backoffice')

    if origen == 'CLIENTE':
        return _split_badge(sender_role='cliente', receiver_role='backoffice')

    return _solid_badge(role='backoffice')


def build_delivery_workflow_badge(delivery):
    estado = getattr(delivery, 'estado', '') or ''

    if estado in {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}:
        return _solid_badge(role='driver', label=_('Delivered'))

    if estado in {'ASIGNADA', 'EN_RUTA'}:
        return _split_badge(sender_role='backoffice', receiver_role='driver')

    return _solid_badge(role='backoffice')
