from django.utils.translation import gettext as _


ROLE_BADGE_STYLES = {
	'backoffice': {
		'label': _('BackOffice'),
		'color': '#facc15',
		'text_color': '#1f2937',
	},
	'driver': {
		'label': _('Driver'),
		'color': '#86efac',
		'text_color': '#14532d',
	},
	'vendedor': {
		'label': _('Vendor'),
		'color': '#93c5fd',
		'text_color': '#1e3a8a',
	},
	'seleccionador': {
		'label': _('Picking'),
		'color': '#fca5a5',
		'text_color': '#7f1d1d',
	},
}


def _get_role_style(role):
	if not role:
		return None
	return ROLE_BADGE_STYLES.get(role)


def build_role_badge(role, label=None):
	role_style = _get_role_style(role)
	if not role_style:
		return None
	return {
		'kind': 'solid',
		'label': label or role_style['label'],
		'sender_role': role,
		'receiver_role': role,
		'sender_label': role_style['label'],
		'receiver_label': role_style['label'],
		'color_start': role_style['color'],
		'color_end': role_style['color'],
		'text_color': role_style['text_color'],
	}


def build_transition_badge(sender_role, receiver_role, label=None):
	sender_style = _get_role_style(sender_role)
	receiver_style = _get_role_style(receiver_role)
	if sender_style and receiver_style:
		computed_label = label or _('%(sender)s -> %(receiver)s') % {
			'sender': sender_style['label'],
			'receiver': receiver_style['label'],
		}
		return {
			'kind': 'split',
			'label': computed_label,
			'sender_role': sender_role,
			'receiver_role': receiver_role,
			'sender_label': sender_style['label'],
			'receiver_label': receiver_style['label'],
			'color_start': sender_style['color'],
			'color_end': receiver_style['color'],
			'text_color': '#111827',
		}
	if receiver_style:
		return build_role_badge(receiver_role, label=label)
	if sender_style:
		return build_role_badge(sender_role, label=label)
	return None


def build_order_workflow_badge(order):
	state = getattr(order, 'estado', '')
	origin = getattr(order, 'origen', '')
	invoice = getattr(order, 'invoice', None)

	if state == 'RECIBIDO':
		if origin == 'VENDEDOR':
			return build_transition_badge('vendedor', 'backoffice')
		return build_role_badge('backoffice')
	if state == 'EN_GESTION':
		return build_role_badge('backoffice')
	if state in {'LISTO_PARA_PICKING', 'PARA_VERIFICAR'}:
		return build_transition_badge('backoffice', 'seleccionador')
	if state == 'VERIFICADO_AJUSTADO':
		if getattr(order, 'seleccionador_id', None):
			return build_transition_badge('seleccionador', 'backoffice')
		return build_role_badge('backoffice')
	if state == 'INVOICE_GENERADA':
		if invoice is not None and getattr(invoice, 'metodo_entrega', '') == 'RUTA_DRIVER' and getattr(invoice, 'driver_id', None):
			return build_transition_badge('backoffice', 'driver')
		return build_role_badge('backoffice')
	if state == 'DESPACHADO':
		if invoice is not None and getattr(invoice, 'driver_id', None):
			return build_role_badge('driver')
		return build_role_badge('backoffice')
	return None


def build_delivery_workflow_badge(delivery):
	state = getattr(delivery, 'estado', '')
	if state == 'ASIGNADA':
		return build_transition_badge('backoffice', 'driver')
	if state in {'EN_RUTA', 'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}:
		return build_role_badge('driver')
	return None