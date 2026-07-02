from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from config.pedidos.models import Pedido


def _has_active_invoice(pedido):
	invoice = getattr(pedido, 'invoice', None)
	return invoice is not None and invoice.estado == 'GENERADA'


def _pedido_had_invoice_reversed(pedido, *, reversed_invoice_pedido_ids=None):
	if reversed_invoice_pedido_ids is not None:
		return pedido.id in reversed_invoice_pedido_ids
	from config.inventario.models import InventarioMovimiento

	return InventarioMovimiento.objects.filter(
		pedido_id=pedido.id,
		tipo='ANULACION_PEDIDO',
		referencia__startswith='INV-',
	).exists()


def _should_show_in_backoffice_review(pedido, *, reversed_invoice_pedido_ids=None):
	if pedido.estado != 'VERIFICADO_AJUSTADO':
		return False
	invoice = getattr(pedido, 'invoice', None)
	if invoice and invoice.estado == 'ANULADA':
		return False
	if _pedido_had_invoice_reversed(pedido, reversed_invoice_pedido_ids=reversed_invoice_pedido_ids):
		return False
	return True


CRM_COLUMN_KEYS = (
	'confirmed',
	'picking_pending',
	'backoffice_review',
	'driver',
	'pickup',
	'delivered',
)

CRM_COLUMN_CONFIG = {
	'confirmed': {
		'title': _('Confirmed orders'),
		'accent': '#3b82f6',
		'statuses': {'RECIBIDO', 'EN_GESTION', 'LISTO_PARA_PICKING'},
	},
	'picking_pending': {
		'title': _('Pending picking'),
		'accent': '#06b6d4',
		'statuses': {'PARA_VERIFICAR'},
	},
	'backoffice_review': {
		'title': _('BackOffice / invoice'),
		'accent': '#14b8a6',
		'statuses': {'VERIFICADO_AJUSTADO'},
	},
	'driver': {
		'title': _('Driver'),
		'accent': '#f59e0b',
		'statuses': {'INVOICE_GENERADA'},
	},
	'pickup': {
		'title': _('Customer pickup'),
		'accent': '#8b5cf6',
		'statuses': {'INVOICE_GENERADA'},
	},
	'delivered': {
		'title': _('Delivered'),
		'accent': '#22c55e',
		'statuses': {'DESPACHADO'},
	},
}

DELIVERED_DELIVERY_STATUSES = {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}


@dataclass
class CrmPipelineCard:
	column_key: str
	pedido_id: int
	customer_name: str
	manager_name: str
	total: Decimal
	created_at: datetime
	status_label: str
	detail_url: str
	is_blocked: bool = False
	is_pickup: bool = False


@dataclass
class CrmPipelineColumn:
	key: str
	title: str
	accent: str
	cards: list
	column_total: Decimal
	period_total: Decimal
	card_count: int


def _pedido_origin_label(origin):
	return {
		'CLIENTE': _('Customer'),
		'VENDEDOR': _('Vendor'),
		'BACKOFFICE': _('BackOffice'),
	}.get(origin, origin)


def _pedido_state_label(state):
	return {
		'RECIBIDO': _('Received'),
		'EN_GESTION': _('In progress'),
		'LISTO_PARA_PICKING': _('Ready for picking'),
		'PARA_VERIFICAR': _('Pending verification'),
		'VERIFICADO_AJUSTADO': _('Verified and adjusted'),
		'INVOICE_GENERADA': _('Invoice generated'),
		'DESPACHADO': _('Dispatched'),
	}.get(state, state)


def _user_display_name(user):
	if not user:
		return ''
	return (user.get_full_name() or '').strip() or user.username


def _manager_label(pedido):
	if pedido.vendedor_id:
		return _user_display_name(pedido.vendedor)
	return str(_pedido_origin_label(pedido.origen))


def _period_bounds(period, *, reference_date=None):
	today = reference_date or timezone.localdate()
	if period == 'week':
		start = today - timedelta(days=today.weekday())
		return start, today
	if period == 'month':
		return today.replace(day=1), today
	return today, today


def _completion_datetime(pedido):
	invoice = getattr(pedido, 'invoice', None)
	delivery = getattr(invoice, 'delivery', None) if invoice else None
	if delivery and delivery.delivered_at:
		return delivery.delivered_at
	if pedido.estado == 'DESPACHADO':
		return pedido.actualizada_en
	return None


def _completion_date(pedido):
	completed_at = _completion_datetime(pedido)
	return timezone.localtime(completed_at).date() if completed_at else None


def _created_in_period(pedido, period_start, period_end):
	created = timezone.localtime(pedido.creada_en).date()
	return period_start <= created <= period_end


def _is_completed_pedido(pedido):
	invoice = getattr(pedido, 'invoice', None)
	delivery = getattr(invoice, 'delivery', None) if invoice else None
	if delivery and delivery.estado in DELIVERED_DELIVERY_STATUSES:
		return True
	return pedido.estado == 'DESPACHADO'


def _completed_column_key(pedido):
	invoice = getattr(pedido, 'invoice', None)
	if invoice and invoice.metodo_entrega == 'CUSTOMER_PICK_UP':
		return 'pickup'
	return 'delivered'


def _resolve_column_key(pedido, *, reversed_invoice_pedido_ids=None):
	estado = pedido.estado
	invoice = getattr(pedido, 'invoice', None)
	delivery = getattr(invoice, 'delivery', None) if invoice else None

	if delivery and delivery.estado in DELIVERED_DELIVERY_STATUSES:
		return _completed_column_key(pedido)
	if estado in CRM_COLUMN_CONFIG['confirmed']['statuses']:
		return 'confirmed'
	if estado in CRM_COLUMN_CONFIG['picking_pending']['statuses']:
		return 'picking_pending'
	if estado in CRM_COLUMN_CONFIG['backoffice_review']['statuses']:
		if _should_show_in_backoffice_review(pedido, reversed_invoice_pedido_ids=reversed_invoice_pedido_ids):
			return 'backoffice_review'
		return None
	if estado == 'INVOICE_GENERADA':
		if not _has_active_invoice(pedido):
			return None
		if invoice and invoice.metodo_entrega == 'RUTA_DRIVER':
			return 'driver'
		if invoice and invoice.metodo_entrega == 'CUSTOMER_PICK_UP':
			return 'pickup'
		return 'backoffice_review'
	if estado == 'DESPACHADO':
		return _completed_column_key(pedido)
	return None


def _detail_url(pedido):
	invoice = getattr(pedido, 'invoice', None)
	if invoice is not None:
		return reverse('backoffice_invoice_detail', args=[invoice.pk])
	return reverse('backoffice_pedido_detalle', args=[pedido.id])


def _base_queryset():
	return (
		Pedido.objects.select_related(
			'cliente',
			'vendedor',
			'seleccionador',
			'invoice',
			'invoice__delivery',
			'invoice__driver',
		)
		.exclude(estado='CANCELADO')
		.exclude(canal_toma='QUICKBOOKS_IMPORT')
		.order_by('-creada_en')
	)


def _apply_filters(queryset, *, search_term, vendedor_id, cliente_id):
	if vendedor_id:
		queryset = queryset.filter(vendedor_id=vendedor_id)
	if cliente_id:
		queryset = queryset.filter(cliente_id=cliente_id)
	if search_term:
		queryset = queryset.filter(
			Q(cliente__nombre_empresa__icontains=search_term)
			| Q(id__icontains=search_term)
			| Q(vendedor__first_name__icontains=search_term)
			| Q(vendedor__last_name__icontains=search_term)
			| Q(vendedor__username__icontains=search_term)
		)
	return queryset


def _should_show_card(pedido, *, column_key, period_start, period_end):
	if column_key in {'delivered', 'pickup'} and _is_completed_pedido(pedido):
		completion_date = _completion_date(pedido)
		if completion_date is None:
			return False
		return period_start <= completion_date <= period_end
	return True


def _build_card(pedido, column_key):
	invoice = getattr(pedido, 'invoice', None)
	delivery = getattr(invoice, 'delivery', None) if invoice else None
	status_label = str(_pedido_state_label(pedido.estado))
	if delivery:
		status_label = str(delivery.get_estado_display())
	elif invoice:
		status_label = str(invoice.get_metodo_entrega_display())

	return CrmPipelineCard(
		column_key=column_key,
		pedido_id=pedido.id,
		customer_name=pedido.cliente.nombre_empresa,
		manager_name=_manager_label(pedido),
		total=pedido.total,
		created_at=pedido.creada_en,
		status_label=status_label,
		detail_url=_detail_url(pedido),
		is_blocked=bool(pedido.picking_bloqueado),
		is_pickup=bool(invoice and invoice.metodo_entrega == 'CUSTOMER_PICK_UP'),
	)


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


def build_crm_pipeline(*, period='today', search_term='', vendedor_id=None, cliente_id=None, reference_date: Optional[date] = None):
	period_start, period_end = _period_bounds(period, reference_date=reference_date)
	queryset = _apply_filters(
		_base_queryset(),
		search_term=(search_term or '').strip(),
		vendedor_id=vendedor_id,
		cliente_id=cliente_id,
	)
	pedidos = list(queryset)
	reversed_invoice_pedido_ids = _load_reversed_invoice_pedido_ids([pedido.id for pedido in pedidos])

	cards_by_column = {key: [] for key in CRM_COLUMN_KEYS}
	for pedido in pedidos:
		column_key = _resolve_column_key(pedido, reversed_invoice_pedido_ids=reversed_invoice_pedido_ids)
		if column_key is None:
			continue
		if not _should_show_card(pedido, column_key=column_key, period_start=period_start, period_end=period_end):
			continue
		cards_by_column[column_key].append(_build_card(pedido, column_key))

	columns = []
	pedido_map = {pedido.id: pedido for pedido in pedidos}
	for key in CRM_COLUMN_KEYS:
		config = CRM_COLUMN_CONFIG[key]
		column_cards = cards_by_column[key]
		column_total = sum((card.total for card in column_cards), start=Decimal('0.00'))
		period_total = Decimal('0.00')
		for card in column_cards:
			pedido = pedido_map.get(card.pedido_id)
			if pedido and _created_in_period(pedido, period_start, period_end):
				period_total += card.total

		columns.append(
			CrmPipelineColumn(
				key=key,
				title=str(config['title']),
				accent=config['accent'],
				cards=column_cards,
				column_total=column_total,
				period_total=period_total,
				card_count=len(column_cards),
			)
		)

	return {
		'columns': columns,
		'period': period,
		'period_start': period_start,
		'period_end': period_end,
		'search_term': (search_term or '').strip(),
		'vendedor_id': vendedor_id,
		'cliente_id': cliente_id,
	}
