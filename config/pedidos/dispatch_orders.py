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


def _pedido_rows_for_statuses(*, statuses, pedido_queryset):
	rows = []
	for pedido in pedido_queryset.filter(estado__in=statuses).order_by('-creada_en'):
		rows.append(
			DispatchOrderRow(
				row_key=f'order-{pedido.id}',
				record_type='order',
				source_id=pedido.id,
				customer_name=pedido.cliente.nombre_empresa,
				selector_name=_selector_display_name(getattr(pedido, 'seleccionador', None)),
				origin_label=str(_pedido_origin_label(pedido.origen)),
				status_label=str(_pedido_state_label(pedido.estado)),
				status_badge_class=_pedido_status_badge_class(pedido.estado),
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
	return Pedido.objects.select_related(
		'cliente__usuario',
		'vendedor',
		'seleccionador',
		'invoice',
		'invoice__driver',
	).prefetch_related('items')


def get_dispatch_order_counts():
	open_quotes = _open_quote_queryset()
	pedidos = Pedido.objects.all()
	return {
		'pending_count': (
			open_quotes.filter(estado__in=QUOTE_PENDING_STATUSES).count()
			+ pedidos.filter(estado__in=PEDIDO_PENDING_STATUSES).count()
		),
		'in_progress_count': pedidos.filter(estado__in=PEDIDO_IN_PROGRESS_STATUSES).count(),
		'completed_count': (
			pedidos.filter(estado__in=PEDIDO_COMPLETED_STATUSES).count()
			+ open_quotes.filter(estado__in=QUOTE_COMPLETED_STATUSES).count()
		),
		'cancelled_count': (
			pedidos.filter(estado__in=PEDIDO_CANCELLED_STATUSES).count()
			+ open_quotes.filter(estado__in=QUOTE_CANCELLED_STATUSES).count()
		),
		'pending_requests_count': open_quotes.filter(estado__in=QUOTE_PENDING_STATUSES).count(),
		'pending_dispatch_count': pedidos.filter(estado__in=PEDIDO_PENDING_STATUSES).count(),
	}


def build_dispatch_order_page(*, view_mode, page_number, page_size):
	pedido_queryset = _pedido_base_queryset()

	if view_mode == 'in-progress':
		rows = _pedido_rows_for_statuses(statuses=PEDIDO_IN_PROGRESS_STATUSES, pedido_queryset=pedido_queryset)
	elif view_mode == 'completed':
		rows = _pedido_rows_for_statuses(statuses=PEDIDO_COMPLETED_STATUSES, pedido_queryset=pedido_queryset)
		rows.extend(_quote_rows_for_statuses(statuses=QUOTE_COMPLETED_STATUSES))
	elif view_mode == 'cancelled':
		rows = _pedido_rows_for_statuses(statuses=PEDIDO_CANCELLED_STATUSES, pedido_queryset=pedido_queryset)
		rows.extend(_quote_rows_for_statuses(statuses=QUOTE_CANCELLED_STATUSES))
	else:
		view_mode = 'pending'
		rows = _quote_rows_for_statuses(statuses=QUOTE_PENDING_STATUSES)
		rows.extend(_pedido_rows_for_statuses(statuses=PEDIDO_PENDING_STATUSES, pedido_queryset=pedido_queryset))

	rows.sort(key=lambda row: row.date, reverse=True)
	page_obj = Paginator(rows, page_size).get_page(page_number)
	return view_mode, page_obj
