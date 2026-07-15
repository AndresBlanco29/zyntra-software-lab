from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.translation import gettext as _

from config.core.workflow_badges import build_order_workflow_badge, build_quote_workflow_badge
from config.cotizaciones.models import Cotizacion
from config.pedidos.models import Pedido


QUOTE_PENDING_STATUSES = ('ENVIADA', 'LISTA_PARA_CONFIRMACION', 'CONFIRMADA_CLIENTE')
QUOTE_CANCELLED_STATUSES = ('CANCELADA_CLIENTE',)
QUOTE_COMPLETED_STATUSES = ('APROBADA', 'RECHAZADA', 'BORRADOR')

PEDIDO_PENDING_STATUSES = {'RECIBIDO', 'EN_GESTION', 'LISTO_PARA_PICKING'}
PEDIDO_IN_PROGRESS_STATUSES = {'PARA_VERIFICAR', 'VERIFICADO_AJUSTADO', 'INVOICE_GENERADA'}
PEDIDO_COMPLETED_STATUSES = {'DESPACHADO'}
PEDIDO_CANCELLED_STATUSES = {'CANCELADO'}
DELIVERED_DELIVERY_STATUSES = {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}

DISPATCH_PROCESS_STAGES = (
	('pending', _('Pending orders'), 'primary'),
	('quotation-sent', _('Quotation sent to client'), 'info'),
	('purchase-order', _('Purchase order · BackOffice'), 'warning'),
	('sent-to-picking', _('Sent to picking'), 'warning'),
	('picking-returned', _('Picking adjusted and returned'), 'info'),
	('sent-to-driver', _('Sent to driver'), 'primary'),
	('customer-pickup', _('Customer pick up'), 'info'),
	('completed', _('Completed orders'), 'success'),
	('cancelled', _('Cancelled orders'), 'secondary'),
)

LEGACY_IN_PROGRESS_STAGE_KEYS = (
	'sent-to-picking',
	'picking-returned',
	'sent-to-driver',
	'customer-pickup',
)


@dataclass
class DispatchOrderRow:
	row_key: str
	record_type: str
	source_id: int
	customer_name: str
	selector_name: str
	origin_label: str
	status_label: str
	status_badge_class: str
	total: Decimal
	date: datetime
	detail_url: str
	workflow_badge: Optional[object] = None
	display_ref: str = ''


def _quote_status_badge_class(estado):
	if estado == 'CONFIRMADA_CLIENTE':
		return 'bg-success'
	if estado == 'CANCELADA_CLIENTE':
		return 'bg-secondary'
	if estado == 'LISTA_PARA_CONFIRMACION':
		return 'bg-warning text-dark'
	if estado == 'RECHAZADA':
		return 'bg-danger'
	if estado == 'APROBADA':
		return 'bg-dark'
	return 'bg-primary'


def _pedido_status_badge_class(estado):
	if estado == 'DESPACHADO':
		return 'bg-success'
	if estado == 'CANCELADO':
		return 'bg-secondary'
	if estado in {'EN_GESTION', 'PARA_VERIFICAR', 'VERIFICADO_AJUSTADO', 'INVOICE_GENERADA'}:
		return 'bg-warning text-dark'
	return 'bg-info text-dark'


def _quote_origin_label(cotizacion):
	if cotizacion.vendedor_id:
		return _('Sales')
	return _('Customer')


def _pedido_state_label(state):
	return {
		'RECIBIDO': _('Received'),
		'EN_GESTION': _('In progress'),
		'LISTO_PARA_PICKING': _('Ready for picking'),
		'PARA_VERIFICAR': _('Pending verification'),
		'VERIFICADO_AJUSTADO': _('Verified and adjusted'),
		'INVOICE_GENERADA': _('Invoice generated'),
		'DESPACHADO': _('Dispatched'),
		'CANCELADO': _('Cancelled'),
	}.get(state, state)


def _pedido_origin_label(origin):
	return {
		'CLIENTE': _('Customer'),
		'VENDEDOR': _('Sales'),
		'BACKOFFICE': _('BackOffice'),
	}.get(origin, origin)


def _selector_display_name(user):
	if not user:
		return ''
	return (user.get_full_name() or '').strip() or user.username


def _load_reversed_invoice_pedido_ids(pedido_ids):
	if not pedido_ids:
		return set()
	from config.inventario.models import InventarioMovimiento

	return set(
		InventarioMovimiento.objects.filter(
			pedido_id__in=pedido_ids,
			tipo='ANULACION_PEDIDO',
			referencia__startswith='INV-',
		).values_list('pedido_id', flat=True)
	)


def _pedido_is_completed(pedido):
	if pedido.estado == 'DESPACHADO':
		return True
	invoice = getattr(pedido, 'invoice', None)
	if invoice and invoice.estado == 'GENERADA':
		delivery = getattr(invoice, 'delivery', None)
		if delivery and delivery.estado in DELIVERED_DELIVERY_STATUSES:
			return True
	return False


def _pedido_is_cancelled(pedido, *, reversed_invoice_pedido_ids=None):
	if pedido.estado == 'CANCELADO':
		return True
	invoice = getattr(pedido, 'invoice', None)
	if invoice and invoice.estado == 'ANULADA':
		return True
	if reversed_invoice_pedido_ids is not None:
		return pedido.id in reversed_invoice_pedido_ids
	return pedido.id in _load_reversed_invoice_pedido_ids([pedido.id])


def _get_pedido_delivery(pedido):
	from config.core.workflow_badges import _safe_related

	invoice = _safe_related(pedido, 'invoice')
	return _safe_related(invoice, 'delivery')


def _pedido_manage_url(pedido):
	from config.core.workflow_badges import _safe_related

	invoice = _safe_related(pedido, 'invoice')
	if invoice is not None and getattr(invoice, 'estado', '') != 'ANULADA':
		return reverse('backoffice_invoice_detail', args=[invoice.id])
	return reverse('backoffice_pedido_detalle', args=[pedido.id])


def _empty_process_stage_buckets():
	return {stage_key: {'pedidos': [], 'quotes': []} for stage_key, _label, _style in DISPATCH_PROCESS_STAGES}


def _resolve_quote_process_stage(cotizacion):
	return {
		'ENVIADA': 'pending',
		'LISTA_PARA_CONFIRMACION': 'quotation-sent',
		'CONFIRMADA_CLIENTE': 'purchase-order',
		'CANCELADA_CLIENTE': 'cancelled',
		'APROBADA': 'completed',
		'RECHAZADA': 'completed',
		'BORRADOR': 'completed',
	}.get(cotizacion.estado)


def _resolve_pedido_process_stage(pedido, *, reversed_invoice_pedido_ids=None):
	if _pedido_is_cancelled(pedido, reversed_invoice_pedido_ids=reversed_invoice_pedido_ids):
		return 'cancelled'
	if _pedido_is_completed(pedido):
		return 'completed'

	estado = pedido.estado
	delivery = _get_pedido_delivery(pedido)
	invoice = getattr(pedido, 'invoice', None)

	if estado == 'RECIBIDO':
		return 'pending'
	if estado in {'EN_GESTION', 'LISTO_PARA_PICKING'}:
		return 'purchase-order'
	if estado == 'PARA_VERIFICAR':
		return 'sent-to-picking'
	if estado == 'VERIFICADO_AJUSTADO':
		return 'picking-returned'
	if estado == 'INVOICE_GENERADA' or (invoice and invoice.estado == 'GENERADA'):
		if delivery and getattr(delivery, 'is_customer_pickup', False):
			return 'customer-pickup'
		return 'sent-to-driver'
	return None


def _classify_dispatch_records(*, pedidos, quotes):
	reversed_invoice_pedido_ids = _load_reversed_invoice_pedido_ids([pedido.id for pedido in pedidos])
	buckets = _empty_process_stage_buckets()

	for pedido in pedidos:
		stage = _resolve_pedido_process_stage(pedido, reversed_invoice_pedido_ids=reversed_invoice_pedido_ids)
		if stage:
			buckets[stage]['pedidos'].append(pedido)

	for cotizacion in quotes:
		stage = _resolve_quote_process_stage(cotizacion)
		if stage:
			buckets[stage]['quotes'].append(cotizacion)

	for stage_key in buckets:
		buckets[stage_key]['pedidos'].sort(key=lambda pedido: pedido.creada_en, reverse=True)
		buckets[stage_key]['quotes'].sort(key=lambda cotizacion: cotizacion.fecha, reverse=True)
	return buckets


def _normalize_dispatch_view_mode(view_mode):
	view_mode = (view_mode or 'pending').strip()
	valid_stage_keys = {stage_key for stage_key, _label, _style in DISPATCH_PROCESS_STAGES}
	if view_mode in valid_stage_keys:
		return view_mode
	if view_mode == 'in-progress':
		return 'sent-to-picking'
	return 'pending'


def get_dispatch_process_stages(*, stage_counts=None):
	counts = stage_counts or {}
	return [
		{
			'key': stage_key,
			'label': str(label),
			'button_style': button_style,
			'count': counts.get(stage_key, 0),
		}
		for stage_key, label, button_style in DISPATCH_PROCESS_STAGES
	]


def _resolve_quote_operational_status(cotizacion):
	return {
		'ENVIADA': (_('Customer request received'), 'bg-primary'),
		'LISTA_PARA_CONFIRMACION': (_('Quotation sent to client'), 'bg-info text-dark'),
		'CONFIRMADA_CLIENTE': (_('Confirmed by client'), 'bg-success'),
		'CANCELADA_CLIENTE': (_('Cancelled by client'), 'bg-secondary'),
		'APROBADA': (_('Approved'), 'bg-dark'),
		'RECHAZADA': (_('Rejected'), 'bg-danger'),
		'BORRADOR': (_('Draft'), 'bg-secondary'),
	}.get(cotizacion.estado, (str(cotizacion.get_estado_display()), _quote_status_badge_class(cotizacion.estado)))


def _resolve_pedido_operational_status(pedido):
	estado = pedido.estado
	delivery = _get_pedido_delivery(pedido)
	invoice = getattr(pedido, 'invoice', None)

	if estado == 'CANCELADO':
		return _('Cancelled'), _pedido_status_badge_class('CANCELADO')

	if delivery and delivery.estado in DELIVERED_DELIVERY_STATUSES:
		return _('Completed'), 'bg-success'

	if estado == 'DESPACHADO':
		return _('Completed'), 'bg-success'

	if delivery and delivery.estado == 'EN_RUTA':
		return _('Out for delivery'), 'bg-primary'

	if estado == 'INVOICE_GENERADA' or (invoice and invoice.estado == 'GENERADA'):
		if delivery and getattr(delivery, 'is_customer_pickup', False):
			return _('Customer pick up'), 'bg-info text-dark'
		return _('Sent to driver'), 'bg-primary'

	if estado == 'VERIFICADO_AJUSTADO':
		return _('Picking adjusted and returned'), 'bg-info text-dark'

	if estado == 'PARA_VERIFICAR':
		return _('Sent to picking'), 'bg-warning text-dark'

	if estado == 'LISTO_PARA_PICKING':
		return _('Ready for picking'), 'bg-warning text-dark'

	if estado == 'EN_GESTION':
		return _('Purchase order · BackOffice'), 'bg-warning text-dark'

	if estado == 'RECIBIDO':
		return _('Pending order'), 'bg-info text-dark'

	return _pedido_state_label(estado), _pedido_status_badge_class(estado)


def _pedido_status_display(pedido, *, bucket):
	if bucket == 'cancelled':
		return str(_('Cancelled')), _pedido_status_badge_class('CANCELADO')
	label, badge_class = _resolve_pedido_operational_status(pedido)
	return str(label), badge_class


def _quote_rows_from_quotes(*, quotes, bucket):
	rows = []
	for cotizacion in quotes:
		status_label, status_badge_class = _resolve_quote_operational_status(cotizacion)
		rows.append(
			DispatchOrderRow(
				row_key=f'quote-{cotizacion.id}',
				record_type='quote',
				source_id=cotizacion.id,
				customer_name=cotizacion.cliente.nombre_empresa,
				selector_name='',
				origin_label=str(_quote_origin_label(cotizacion)),
				status_label=str(status_label),
				status_badge_class=status_badge_class,
				total=cotizacion.total,
				date=cotizacion.fecha,
				detail_url=reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]),
				workflow_badge=build_quote_workflow_badge(cotizacion),
				display_ref=str(cotizacion.id),
			)
		)
	return rows


def _quote_rows_for_statuses(*, statuses):
	queryset = (
		Cotizacion.objects.select_related('cliente__usuario', 'vendedor', 'pedido_generado')
		.filter(estado__in=statuses, pedido_generado__isnull=True)
		.order_by('-fecha')
	)
	return _quote_rows_from_quotes(quotes=list(queryset), bucket='')


def _pedido_rows_from_pedidos(*, pedidos, bucket):
	rows = []
	for pedido in pedidos:
		status_label, status_badge_class = _pedido_status_display(pedido, bucket=bucket)
		rows.append(
			DispatchOrderRow(
				row_key=f'order-{pedido.id}',
				record_type='order',
				source_id=pedido.id,
				customer_name=pedido.cliente.nombre_empresa,
				selector_name=_selector_display_name(getattr(pedido, 'seleccionador', None)),
				origin_label=str(_pedido_origin_label(pedido.origen)),
				status_label=status_label,
				status_badge_class=status_badge_class,
				total=pedido.total,
				date=pedido.creada_en,
				detail_url=_pedido_manage_url(pedido),
				workflow_badge=build_order_workflow_badge(pedido),
				display_ref=pedido.numero_display,
			)
		)
	return rows


def _open_quote_queryset():
	return Cotizacion.objects.filter(pedido_generado__isnull=True)


def _pedido_base_queryset():
	return Pedido.objects.exclude(canal_toma='QUICKBOOKS_IMPORT').select_related(
		'cliente__usuario',
		'vendedor',
		'seleccionador',
		'invoice',
		'invoice__driver',
		'invoice__delivery',
	).prefetch_related('items')


def _load_open_quotes():
	return list(
		_open_quote_queryset()
		.select_related('cliente__usuario', 'vendedor', 'pedido_generado')
		.order_by('-fecha')
	)


def _count_process_stage_buckets():
	pedidos = list(_pedido_base_queryset())
	quotes = _load_open_quotes()
	stage_buckets = _classify_dispatch_records(pedidos=pedidos, quotes=quotes)
	return {
		stage_key: len(stage_buckets[stage_key]['pedidos']) + len(stage_buckets[stage_key]['quotes'])
		for stage_key, _label, _style in DISPATCH_PROCESS_STAGES
	}


def get_dispatch_order_counts():
	stage_counts = _count_process_stage_buckets()
	process_stages = get_dispatch_process_stages(stage_counts=stage_counts)
	return {
		'stage_counts': stage_counts,
		'process_stages': process_stages,
		'pending_count': stage_counts.get('pending', 0),
		'in_progress_count': sum(stage_counts.get(stage_key, 0) for stage_key in LEGACY_IN_PROGRESS_STAGE_KEYS),
		'completed_count': stage_counts.get('completed', 0),
		'cancelled_count': stage_counts.get('cancelled', 0),
		'pending_requests_count': stage_counts.get('pending', 0),
		'pending_dispatch_count': stage_counts.get('pending', 0) + stage_counts.get('purchase-order', 0),
	}


def _matches_dispatch_search(row, search_term):
	query = (search_term or '').strip().lower()
	if not query:
		return True
	workflow_label = ''
	if isinstance(row.workflow_badge, dict):
		workflow_label = row.workflow_badge.get('label') or ''
	haystack = ' '.join([
		str(row.source_id),
		str(row.customer_name or ''),
		str(row.selector_name or ''),
		str(row.origin_label or ''),
		str(row.status_label or ''),
		str(workflow_label),
	]).lower()
	return query in haystack


def _filter_dispatch_rows(rows, *, search_term):
	if not (search_term or '').strip():
		return rows
	return [row for row in rows if _matches_dispatch_search(row, search_term)]


def build_dispatch_order_page(*, view_mode, page_number, page_size, search_term=''):
	view_mode = _normalize_dispatch_view_mode(view_mode)
	pedidos = list(_pedido_base_queryset())
	quotes = _load_open_quotes()
	stage_buckets = _classify_dispatch_records(pedidos=pedidos, quotes=quotes)
	selected_bucket = stage_buckets[view_mode]
	rows = _pedido_rows_from_pedidos(pedidos=selected_bucket['pedidos'], bucket=view_mode)
	rows.extend(_quote_rows_from_quotes(quotes=selected_bucket['quotes'], bucket=view_mode))

	rows = _filter_dispatch_rows(rows, search_term=search_term)
	rows.sort(key=lambda row: row.date, reverse=True)
	page_obj = Paginator(rows, page_size).get_page(page_number)
	return view_mode, page_obj
