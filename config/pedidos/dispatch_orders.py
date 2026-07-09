from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.translation import gettext as _

from config.core.workflow_badges import build_order_workflow_badge
from config.cotizaciones.models import Cotizacion
from config.pedidos.models import Pedido


QUOTE_PENDING_STATUSES = ('ENVIADA', 'LISTA_PARA_CONFIRMACION', 'CONFIRMADA_CLIENTE')
QUOTE_CANCELLED_STATUSES = ('CANCELADA_CLIENTE',)
QUOTE_COMPLETED_STATUSES = ('APROBADA', 'RECHAZADA', 'BORRADOR')

PEDIDO_PENDING_STATUSES = {'RECIBIDO', 'LISTO_PARA_PICKING'}
PEDIDO_IN_PROGRESS_STATUSES = {'EN_GESTION', 'PARA_VERIFICAR', 'VERIFICADO_AJUSTADO', 'INVOICE_GENERADA'}
PEDIDO_COMPLETED_STATUSES = {'DESPACHADO'}
PEDIDO_CANCELLED_STATUSES = {'CANCELADO'}
DELIVERED_DELIVERY_STATUSES = {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}


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
		return _('Vendor')
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
		'VENDEDOR': _('Vendor'),
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


def _pedido_dispatch_bucket(pedido, *, reversed_invoice_pedido_ids=None):
	if _pedido_is_cancelled(pedido, reversed_invoice_pedido_ids=reversed_invoice_pedido_ids):
		return 'cancelled'
	if _pedido_is_completed(pedido):
		return 'completed'
	if pedido.estado in PEDIDO_PENDING_STATUSES:
		return 'pending'
	if pedido.estado in PEDIDO_IN_PROGRESS_STATUSES:
		return 'in-progress'
	return None


def _classify_pedidos(pedido_queryset):
	pedidos = list(pedido_queryset)
	reversed_invoice_pedido_ids = _load_reversed_invoice_pedido_ids([pedido.id for pedido in pedidos])
	buckets = {
		'pending': [],
		'in-progress': [],
		'completed': [],
		'cancelled': [],
	}
	for pedido in pedidos:
		bucket = _pedido_dispatch_bucket(pedido, reversed_invoice_pedido_ids=reversed_invoice_pedido_ids)
		if bucket:
			buckets[bucket].append(pedido)
	for bucket_name in buckets:
		buckets[bucket_name].sort(key=lambda pedido: pedido.creada_en, reverse=True)
	return buckets


def _get_pedido_delivery(pedido):
	invoice = getattr(pedido, 'invoice', None)
	if not invoice:
		return None
	return getattr(invoice, 'delivery', None)


def _resolve_pedido_operational_status(pedido):
	estado = pedido.estado
	delivery = _get_pedido_delivery(pedido)
	invoice = getattr(pedido, 'invoice', None)

	if estado == 'CANCELADO':
		return _('Cancelled'), _pedido_status_badge_class('CANCELADO')

	if delivery and delivery.estado in DELIVERED_DELIVERY_STATUSES:
		return _('Delivered'), 'bg-success'

	if estado == 'DESPACHADO':
		return _('Delivered'), 'bg-success'

	if delivery and delivery.estado == 'EN_RUTA':
		return _('With driver'), 'bg-primary'

	if estado == 'INVOICE_GENERADA' or (invoice and invoice.estado == 'GENERADA'):
		if delivery and getattr(delivery, 'is_customer_pickup', False):
			return _('Ready for pickup'), 'bg-info text-dark'
		return _('With driver'), 'bg-primary'

	if estado == 'VERIFICADO_AJUSTADO':
		return _('Verified'), 'bg-info text-dark'

	if estado in {'PARA_VERIFICAR', 'LISTO_PARA_PICKING'}:
		return _('Picking in progress'), 'bg-warning text-dark'

	if estado == 'EN_GESTION':
		return _('In progress'), 'bg-warning text-dark'

	if estado == 'RECIBIDO':
		return _('Received'), 'bg-info text-dark'

	return _pedido_state_label(estado), _pedido_status_badge_class(estado)


def _pedido_status_display(pedido, *, bucket):
	if bucket == 'cancelled':
		return str(_('Cancelled')), _pedido_status_badge_class('CANCELADO')
	label, badge_class = _resolve_pedido_operational_status(pedido)
	return str(label), badge_class


def _quote_rows_for_statuses(*, statuses):
	rows = []
	queryset = (
		Cotizacion.objects.select_related('cliente__usuario', 'vendedor', 'pedido_generado')
		.filter(estado__in=statuses, pedido_generado__isnull=True)
		.order_by('-fecha')
	)
	for cotizacion in queryset:
		rows.append(
			DispatchOrderRow(
				row_key=f'quote-{cotizacion.id}',
				record_type='quote',
				source_id=cotizacion.id,
				customer_name=cotizacion.cliente.nombre_empresa,
				selector_name='',
				origin_label=str(_quote_origin_label(cotizacion)),
				status_label=str(cotizacion.get_estado_display()),
				status_badge_class=_quote_status_badge_class(cotizacion.estado),
				total=cotizacion.total,
				date=cotizacion.fecha,
				detail_url=reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]),
			)
		)
	return rows


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
				detail_url=reverse('backoffice_pedido_detalle', args=[pedido.id]),
				workflow_badge=build_order_workflow_badge(pedido),
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


def _count_pedido_buckets():
	pedidos = list(_pedido_base_queryset())
	reversed_invoice_pedido_ids = _load_reversed_invoice_pedido_ids([pedido.id for pedido in pedidos])
	buckets = {
		'pending': 0,
		'in-progress': 0,
		'completed': 0,
		'cancelled': 0,
	}
	for pedido in pedidos:
		bucket = _pedido_dispatch_bucket(pedido, reversed_invoice_pedido_ids=reversed_invoice_pedido_ids)
		if bucket:
			buckets[bucket] += 1
	return buckets


def get_dispatch_order_counts():
	open_quotes = _open_quote_queryset()
	pedido_buckets = _count_pedido_buckets()
	return {
		'pending_count': (
			open_quotes.filter(estado__in=QUOTE_PENDING_STATUSES).count()
			+ pedido_buckets['pending']
		),
		'in_progress_count': pedido_buckets['in-progress'],
		'completed_count': (
			pedido_buckets['completed']
			+ open_quotes.filter(estado__in=QUOTE_COMPLETED_STATUSES).count()
		),
		'cancelled_count': (
			pedido_buckets['cancelled']
			+ open_quotes.filter(estado__in=QUOTE_CANCELLED_STATUSES).count()
		),
		'pending_requests_count': open_quotes.filter(estado__in=QUOTE_PENDING_STATUSES).count(),
		'pending_dispatch_count': pedido_buckets['pending'],
	}


def _matches_dispatch_search(row, search_term):
	query = (search_term or '').strip().lower()
	if not query:
		return True
	haystack = ' '.join([
		str(row.source_id),
		row.customer_name or '',
		row.selector_name or '',
		str(row.origin_label or ''),
		str(row.status_label or ''),
	]).lower()
	return query in haystack


def _filter_dispatch_rows(rows, *, search_term):
	if not (search_term or '').strip():
		return rows
	return [row for row in rows if _matches_dispatch_search(row, search_term)]


def build_dispatch_order_page(*, view_mode, page_number, page_size, search_term=''):
	pedido_buckets = _classify_pedidos(_pedido_base_queryset())

	if view_mode == 'in-progress':
		rows = _pedido_rows_from_pedidos(pedidos=pedido_buckets['in-progress'], bucket='in-progress')
	elif view_mode == 'completed':
		rows = _pedido_rows_from_pedidos(pedidos=pedido_buckets['completed'], bucket='completed')
		rows.extend(_quote_rows_for_statuses(statuses=QUOTE_COMPLETED_STATUSES))
	elif view_mode == 'cancelled':
		rows = _pedido_rows_from_pedidos(pedidos=pedido_buckets['cancelled'], bucket='cancelled')
		rows.extend(_quote_rows_for_statuses(statuses=QUOTE_CANCELLED_STATUSES))
	else:
		view_mode = 'pending'
		rows = _quote_rows_for_statuses(statuses=QUOTE_PENDING_STATUSES)
		rows.extend(_pedido_rows_from_pedidos(pedidos=pedido_buckets['pending'], bucket='pending'))

	rows = _filter_dispatch_rows(rows, search_term=search_term)
	rows.sort(key=lambda row: row.date, reverse=True)
	page_obj = Paginator(rows, page_size).get_page(page_number)
	return view_mode, page_obj
