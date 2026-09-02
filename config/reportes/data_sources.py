"""Canonical report data builders for Reports Center / Business Overview.

Reuse existing availability, landed cost, AR, and BI helpers — do not reinvent
inventory or receivables math here.
"""

from calendar import monthrange
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

from config.clientes.balance_summary import build_customers_receivables_summary
from config.clientes.models import Cliente
from config.facturacion.models import Delivery, Invoice, InvoiceItem
from config.inventario.availability import availability_snapshot
from config.inventario.models import CompraProveedor, InventarioMovimiento
from config.pedidos.models import Pedido
from config.productos.landed_cost import resolve_effective_cost
from config.productos.models import Presentacion
from config.reportes.bi_metrics import LOW_STOCK_THRESHOLD, enrich_product_rows_with_margin


DECIMAL_ZERO = Decimal('0.00')
QUICKBOOKS_IMPORT_CHANNEL = 'QUICKBOOKS_IMPORT'
CANCELLED_ORDER_STATUS = 'CANCELADO'
COMPLETED_DELIVERY_STATUSES = frozenset({'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'})

OVERVIEW_PERIOD_CHOICES = (
	('today', _('Today')),
	('last_7_days', _('Last 7 days')),
	('last_30_days', _('Last 30 days')),
	('this_month', _('This month')),
	('previous_month', _('Previous month')),
	('custom', _('Custom range')),
)

REPORT_NAV = (
	{
		'key': 'inventory',
		'title': _('Inventory health'),
		'url_name': 'reportes_inventory',
		'description': _('Out of stock, low stock, and available units'),
	},
	{
		'key': 'stagnant',
		'title': _('Stagnant products'),
		'url_name': 'reportes_stagnant',
		'description': _('Stock on hand with no recent invoiced sales'),
	},
	{
		'key': 'sales',
		'title': _('Sales'),
		'url_name': 'reportes_sales',
		'description': _('Invoiced sales and collections for the period'),
	},
	{
		'key': 'receivables',
		'title': _('Receivables'),
		'url_name': 'reportes_receivables',
		'description': _('Open customer balances and overdue invoices'),
	},
	{
		'key': 'finance',
		'title': _('Finance snapshot'),
		'url_name': 'reportes_finance',
		'description': _('Money in, outstanding AR, and expenses status'),
	},
	{
		'key': 'purchases',
		'title': _('Purchases'),
		'url_name': 'reportes_purchases',
		'description': _('Supplier purchase orders in the period'),
	},
	{
		'key': 'valued',
		'title': _('Valued inventory'),
		'url_name': 'reportes_valued',
		'description': _('Available stock valued at effective cost'),
	},
	{
		'key': 'movements',
		'title': _('Inventory movements'),
		'url_name': 'reportes_movements',
		'description': _('Recent inventory ledger activity'),
	},
	{
		'key': 'bi',
		'title': _('Business Intelligence'),
		'url_name': 'reportes_bi',
		'description': _('Full BI dashboard with charts and exports'),
	},
)


def _as_money(value):
	return value if value is not None else DECIMAL_ZERO


def _sum_money(values):
	total = DECIMAL_ZERO
	for value in values:
		total += _as_money(value)
	return total


def _aware_range(start_date, end_date):
	timezone_info = timezone.get_current_timezone()
	start_datetime = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), timezone_info)
	end_datetime = timezone.make_aware(
		datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
		timezone_info,
	)
	return start_datetime, end_datetime


def parse_overview_period(request):
	"""Parse overview period presets; default is last_30_days.

	Returns a period dict with start/end datetimes plus a comparison window
	of equal length immediately before the selected range.
	"""
	data = request.POST if request.method == 'POST' else request.GET
	today = timezone.localdate()
	preset = (data.get('period') or 'last_30_days').strip().lower()
	start_date = parse_date(data.get('start_date') or '')
	end_date = parse_date(data.get('end_date') or '')

	if preset == 'custom' and start_date and end_date and start_date <= end_date:
		label = _('Custom range')
	elif preset == 'today':
		start_date = today
		end_date = today
		label = _('Today')
	elif preset == 'last_7_days':
		start_date = today - timedelta(days=6)
		end_date = today
		label = _('Last 7 days')
	elif preset == 'this_month':
		start_date = today.replace(day=1)
		end_date = today
		label = _('This month')
	elif preset == 'previous_month':
		first_this_month = today.replace(day=1)
		end_date = first_this_month - timedelta(days=1)
		start_date = end_date.replace(day=1)
		# Clamp to real last day of previous month (end_date already is).
		last_day = monthrange(start_date.year, start_date.month)[1]
		end_date = start_date.replace(day=last_day)
		label = _('Previous month')
	elif preset == 'last_30_days':
		start_date = today - timedelta(days=29)
		end_date = today
		label = _('Last 30 days')
		preset = 'last_30_days'
	else:
		preset = 'last_30_days'
		start_date = today - timedelta(days=29)
		end_date = today
		label = _('Last 30 days')

	start_datetime, end_datetime = _aware_range(start_date, end_date)
	days = (end_date - start_date).days + 1
	comparison_end = start_date - timedelta(days=1)
	comparison_start = comparison_end - timedelta(days=days - 1)
	comparison_start_datetime, comparison_end_datetime = _aware_range(comparison_start, comparison_end)
	# comparison end_datetime is exclusive start of selected period
	comparison_end_datetime = start_datetime

	return {
		'preset': preset,
		'label': label,
		'start_date': start_date,
		'end_date': end_date,
		'start_datetime': start_datetime,
		'end_datetime': end_datetime,
		'comparison_start_date': comparison_start,
		'comparison_end_date': comparison_end,
		'comparison_start_datetime': comparison_start_datetime,
		'comparison_end_datetime': comparison_end_datetime,
		'days': days,
	}


def period_querystring(period):
	"""Build a GET query string for the period selector."""
	parts = [f"period={period.get('preset') or 'last_30_days'}"]
	if period.get('preset') == 'custom':
		parts.append(f"start_date={period['start_date'].isoformat()}")
		parts.append(f"end_date={period['end_date'].isoformat()}")
	return '&'.join(parts)


def invoices_queryset_for_period(period):
	return (
		Invoice.objects.select_related('cliente', 'pedido')
		.filter(
			estado='GENERADA',
			creada_en__gte=period['start_datetime'],
			creada_en__lt=period['end_datetime'],
		)
		.exclude(pedido__canal_toma=QUICKBOOKS_IMPORT_CHANNEL)
	)


def orders_queryset_for_period(period, *, exclude_cancelled=True):
	qs = Pedido.objects.filter(
		creada_en__gte=period['start_datetime'],
		creada_en__lt=period['end_datetime'],
	).exclude(canal_toma=QUICKBOOKS_IMPORT_CHANNEL)
	if exclude_cancelled:
		qs = qs.exclude(estado=CANCELLED_ORDER_STATUS)
	return qs


def count_orders_excluding_cancelled(period):
	"""Order count helper used by sales snapshot and tests."""
	return orders_queryset_for_period(period, exclude_cancelled=True).count()


def build_valued_inventory(*, low_threshold=LOW_STOCK_THRESHOLD):
	"""Value inventory as Available × effective_cost (not legacy stock_disponible)."""
	presentaciones = list(
		Presentacion.objects.select_related('producto', 'producto__marca')
		.filter(producto__activo=True)
		.order_by('producto__nombre', 'nombre')
	)
	ids = [presentacion.id for presentacion in presentaciones]
	availability = availability_snapshot(ids) if ids else {}

	rows = []
	out_of_stock = 0
	low_stock = 0
	with_stock = 0
	inventory_value = DECIMAL_ZERO

	for presentacion in presentaciones:
		snap = availability.get(presentacion.id) or {}
		available = int(snap.get('available', 0) or 0)
		effective = resolve_effective_cost(presentacion)
		unit_cost = _as_money(effective) if effective is not None else DECIMAL_ZERO
		line_value = unit_cost * Decimal(available)
		inventory_value += line_value
		producto = presentacion.producto
		row = {
			'presentacion_id': presentacion.id,
			'name': producto.nombre,
			'presentation': presentacion.nombre,
			'brand': getattr(getattr(producto, 'marca', None), 'nombre', '') or '—',
			'available': available,
			'quick_inventory': int(snap.get('quick_inventory', 0) or 0),
			'unit_cost': unit_cost,
			'value': line_value,
		}
		rows.append(row)
		if available <= 0:
			out_of_stock += 1
		elif available <= low_threshold:
			low_stock += 1
			with_stock += 1
		else:
			with_stock += 1

	rows.sort(key=lambda item: (item['available'] <= 0, -item['available'], item['name'].lower()))

	return {
		'rows': rows,
		'out_of_stock': out_of_stock,
		'low_stock': low_stock,
		'with_stock': with_stock,
		'sku_count': len(rows),
		'inventory_value': inventory_value,
		# Hook: callers compute stagnant value via stagnant_inventory_value().
		'stagnant_value': stagnant_inventory_value,
		'low_threshold': low_threshold,
	}


def stagnant_inventory_value(*, days=30, limit=500):
	"""Helper hook: sum Available × effective_cost for stagnant presentations."""
	rows = build_stagnant_products(days=days, limit=limit)
	return _sum_money(row.get('value') for row in rows)


def build_receivables_snapshot():
	"""Wrap AR truth from balance_summary."""
	summary = build_customers_receivables_summary(Cliente.objects.all())
	return {
		'total_outstanding': _as_money(summary.total_outstanding),
		'overdue_count': int(summary.invoices_overdue or 0),
		'customers_with_balance': int(summary.customers_with_balance or 0),
		'due_this_week': int(summary.invoices_due_this_week or 0),
		'summary': summary,
	}


def build_sales_snapshot(period):
	"""Invoiced sales (GENERADA, exclude QB import) and collected amounts."""
	invoices = list(invoices_queryset_for_period(period).prefetch_related('items'))
	invoiced_sales = _sum_money(invoice.total_neto for invoice in invoices)
	collected_from_invoices = _sum_money(
		_as_money(invoice.total_neto) - _as_money(invoice.saldo_cliente) for invoice in invoices
	)
	outstanding = _sum_money(invoice.saldo_cliente for invoice in invoices)

	deliveries = list(
		Delivery.objects.select_related('invoice')
		.prefetch_related('payments')
		.exclude(invoice__pedido__canal_toma=QUICKBOOKS_IMPORT_CHANNEL)
		.filter(
			estado__in=COMPLETED_DELIVERY_STATUSES,
			delivered_at__gte=period['start_datetime'],
			delivered_at__lt=period['end_datetime'],
		)
	)
	collected_from_deliveries = _sum_money(
		_as_money(delivery.invoice.total_neto) - _as_money(delivery.invoice.saldo_cliente)
		for delivery in deliveries
		if delivery.invoice_id
	)
	# Prefer delivery-close collected when there are closed deliveries; otherwise
	# fall back to invoice total − saldo for the period.
	collected = collected_from_deliveries if deliveries else collected_from_invoices

	order_count = count_orders_excluding_cancelled(period)
	invoice_count = len(invoices)

	return {
		'invoiced_sales': invoiced_sales,
		'collected': collected,
		'collected_from_invoices': collected_from_invoices,
		'collected_from_deliveries': collected_from_deliveries,
		'outstanding': outstanding,
		'invoice_count': invoice_count,
		'order_count': order_count,
		'delivery_count': len(deliveries),
		'invoices': invoices,
		'deliveries': deliveries,
	}


def build_stagnant_products(*, days=30, limit=25):
	"""Presentations with available > 0 and no invoice sales in N days (or never)."""
	today = timezone.localdate()
	cutoff_date = today - timedelta(days=max(int(days), 0))
	timezone_info = timezone.get_current_timezone()
	cutoff_dt = timezone.make_aware(datetime.combine(cutoff_date, datetime.min.time()), timezone_info)

	sold_recently = set(
		InvoiceItem.objects.filter(
			invoice__estado='GENERADA',
			invoice__creada_en__gte=cutoff_dt,
		)
		.exclude(invoice__pedido__canal_toma=QUICKBOOKS_IMPORT_CHANNEL)
		.exclude(presentacion_id__isnull=True)
		.values_list('presentacion_id', flat=True)
		.distinct()
	)

	last_sale_map = dict(
		InvoiceItem.objects.filter(invoice__estado='GENERADA')
		.exclude(invoice__pedido__canal_toma=QUICKBOOKS_IMPORT_CHANNEL)
		.exclude(presentacion_id__isnull=True)
		.values('presentacion_id')
		.annotate(last_sale=Max('invoice__creada_en'))
		.values_list('presentacion_id', 'last_sale')
	)

	valued = build_valued_inventory()
	rows = []
	for row in valued['rows']:
		if row['available'] <= 0:
			continue
		presentacion_id = row['presentacion_id']
		if presentacion_id in sold_recently:
			continue
		last_sale = last_sale_map.get(presentacion_id)
		days_since = None
		if last_sale is not None:
			local_sale = timezone.localtime(last_sale).date()
			days_since = (today - local_sale).days
		rows.append(
			{
				**row,
				'last_sale_at': last_sale,
				'days_since_sale': days_since,
				'never_sold': last_sale is None,
			}
		)
		if limit and len(rows) >= limit:
			break

	return rows


def build_purchases_snapshot(period):
	"""Purchase orders from CompraProveedor for the period (real rows only)."""
	compras = list(
		CompraProveedor.objects.select_related('proveedor')
		.filter(
			fecha_compra__gte=period['start_date'],
			fecha_compra__lte=period['end_date'],
		)
		.order_by('-fecha_compra', '-id')
	)
	total = _sum_money(compra.total for compra in compras)
	received = [compra for compra in compras if compra.estado == CompraProveedor.STATUS_RECEIVED]
	return {
		'count': len(compras),
		'total': total,
		'received_count': len(received),
		'received_total': _sum_money(compra.total for compra in received),
		'rows': [
			{
				'id': compra.id,
				'po_number': compra.po_number or f'#{compra.id}',
				'supplier': compra.proveedor_nombre or getattr(compra.proveedor, 'nombre', '') or '—',
				'date': compra.fecha_compra,
				'status': compra.get_estado_display(),
				'status_code': compra.estado,
				'total': _as_money(compra.total),
			}
			for compra in compras
		],
	}


def build_movements_snapshot(period, *, limit=50):
	"""Recent InventarioMovimiento rows in the period."""
	movimientos = list(
		InventarioMovimiento.objects.select_related(
			'presentacion__producto',
			'creado_por',
		)
		.filter(
			creado_en__gte=period['start_datetime'],
			creado_en__lt=period['end_datetime'],
		)
		.order_by('-creado_en', '-id')[:limit]
	)
	return {
		'count': len(movimientos),
		'rows': [
			{
				'id': mov.id,
				'when': mov.creado_en,
				'product': getattr(getattr(mov.presentacion, 'producto', None), 'nombre', '') or '—',
				'presentation': getattr(mov.presentacion, 'nombre', '') or '—',
				'type': mov.get_tipo_display(),
				'category': mov.get_categoria_display(),
				'quantity': int(mov.cantidad or 0),
				'delta_physical': int(mov.delta_fisico or 0),
				'reference': mov.referencia or '',
			}
			for mov in movimientos
		],
	}


def build_expenses_snapshot():
	"""Gastos module is not available in this project."""
	return {
		'available': False,
		'label': 'N/A',
	}


def build_top_products(period, limit=10):
	"""Top products by revenue from invoice items in the period."""
	invoice_ids = list(invoices_queryset_for_period(period).values_list('id', flat=True))
	if not invoice_ids:
		return []
	items = list(
		InvoiceItem.objects.select_related(
			'presentacion__producto',
			'presentacion__producto__marca',
		).filter(invoice_id__in=invoice_ids)
	)
	rows = enrich_product_rows_with_margin(items)
	return rows[: max(int(limit), 0)]
