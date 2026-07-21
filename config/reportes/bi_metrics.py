"""Business Intelligence metrics helpers for Reports Center."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from config.clientes.models import Cliente
from config.core.profit import calculate_profit_from_revenue, safe_profit_percentage
from config.inventario.models import StockPresentacion
from config.productos.models import Presentacion


DECIMAL_ZERO = Decimal('0.00')
LOW_STOCK_THRESHOLD = 5


def _as_money(value):
	return value if value is not None else DECIMAL_ZERO


def _sum_money(values):
	total = DECIMAL_ZERO
	for value in values:
		total += _as_money(value)
	return total


def _safe_pct(numerator, denominator):
	return safe_profit_percentage(numerator, denominator)


def _delta_row(*, label, current, previous, is_currency=True, invert_positive=False):
	change = current - previous
	is_positive = change >= 0
	if invert_positive:
		is_positive = change <= 0
	display_value = current if is_currency else int(current)
	display_previous = previous if is_currency else int(previous)
	display_change = change if is_currency else int(change)
	return {
		'label': label,
		'value': display_value,
		'previous_value': display_previous,
		'change': display_change,
		'change_percent': _safe_pct(change, previous),
		'is_currency': is_currency,
		'is_positive': is_positive,
		'caption': '',
	}


def build_bi_kpi_cards(*, orders, invoices, deliveries, close_snapshot, comparison_orders, comparison_invoices, comparison_close, invoice_items):
	gross_sales = _sum_money(invoice.total_neto for invoice in invoices)
	prev_sales = _sum_money(invoice.total_neto for invoice in comparison_invoices)
	pending_balance = _sum_money(invoice.saldo_cliente for invoice in invoices)
	prev_pending = _sum_money(invoice.saldo_cliente for invoice in comparison_invoices)
	paid_invoices = sum(1 for invoice in invoices if _as_money(invoice.saldo_cliente) <= DECIMAL_ZERO)
	open_invoices = sum(1 for invoice in invoices if _as_money(invoice.saldo_cliente) > DECIMAL_ZERO)
	collected = close_snapshot.get('collected_amount', DECIMAL_ZERO)
	prev_collected = comparison_close.get('collected_amount', DECIMAL_ZERO)
	units_sold = sum(int(item.cantidad_facturada or 0) for item in invoice_items)
	unique_customers = len({invoice.cliente_id for invoice in invoices if invoice.cliente_id})
	prev_customers = len({invoice.cliente_id for invoice in comparison_invoices if invoice.cliente_id})
	avg_ticket = (gross_sales / len(invoices)) if invoices else DECIMAL_ZERO
	prev_avg_ticket = (prev_sales / len(comparison_invoices)) if comparison_invoices else DECIMAL_ZERO
	avg_per_customer = (gross_sales / unique_customers) if unique_customers else DECIMAL_ZERO
	discounts = _sum_money(
		(_as_money(getattr(item, 'descuento_monto_unitario', 0)) * Decimal(str(item.cantidad_facturada or 0)))
		for item in invoice_items
	)

	cogs = DECIMAL_ZERO
	for item in invoice_items:
		presentacion = getattr(item, 'presentacion', None)
		cost = _as_money(getattr(presentacion, 'costo', 0) if presentacion is not None else 0)
		cogs += cost * Decimal(str(item.cantidad_facturada or 0))
	gross_profit = gross_sales - cogs
	margin_pct = _safe_pct(gross_profit, gross_sales)

	debits = _sum_money(getattr(invoice, 'total_debitos', 0) for invoice in invoices)
	credits = _sum_money(getattr(invoice, 'total_creditos', 0) for invoice in invoices)
	# Net profit approximates gross profit minus debit adjustments (fees/charges) when no full P&L exists.
	net_profit = gross_profit - debits + credits

	cards = [
		_delta_row(label=_('Orders'), current=Decimal(len(orders)), previous=Decimal(len(comparison_orders)), is_currency=False),
		_delta_row(label=_('Invoices'), current=Decimal(len(invoices)), previous=Decimal(len(comparison_invoices)), is_currency=False),
		_delta_row(label=_('Gross sales'), current=gross_sales, previous=prev_sales),
		_delta_row(label=_('Collected'), current=collected, previous=prev_collected),
		_delta_row(label=_('Pending AR'), current=pending_balance, previous=prev_pending, invert_positive=True),
		_delta_row(label=_('Avg. ticket'), current=avg_ticket, previous=prev_avg_ticket),
		{
			'label': _('Gross profit'),
			'value': gross_profit,
			'previous_value': None,
			'change': None,
			'change_percent': margin_pct,
			'is_currency': True,
			'is_positive': True,
			'caption': _('Sales minus catalog cost') + (f' ({margin_pct}%)' if margin_pct is not None else ''),
		},
		{
			'label': _('Net profit (est.)'),
			'value': net_profit,
			'previous_value': None,
			'change': None,
			'change_percent': _safe_pct(net_profit, gross_sales),
			'is_currency': True,
			'is_positive': net_profit >= 0,
			'caption': _('Gross profit − invoice debits + credits'),
		},
		{
			'label': _('Units sold'),
			'value': int(units_sold),
			'previous_value': None,
			'change': None,
			'change_percent': None,
			'is_currency': False,
			'is_positive': True,
			'caption': _('Total billed packages in the period'),
		},
		_delta_row(label=_('Active customers'), current=Decimal(unique_customers), previous=Decimal(prev_customers), is_currency=False),
		{
			'label': _('Avg. per customer'),
			'value': avg_per_customer,
			'previous_value': None,
			'change': None,
			'change_percent': None,
			'is_currency': True,
			'is_positive': True,
			'caption': _('Gross sales divided by customers with invoices'),
		},
		{
			'label': _('Paid invoices'),
			'value': int(paid_invoices),
			'previous_value': None,
			'change': None,
			'change_percent': None,
			'is_currency': False,
			'is_positive': True,
			'caption': _('Invoices with zero open balance'),
		},
		{
			'label': _('Open invoices'),
			'value': int(open_invoices),
			'previous_value': None,
			'change': None,
			'change_percent': None,
			'is_currency': False,
			'is_positive': open_invoices == 0,
			'caption': _('Invoices still carrying balance'),
		},
		{
			'label': _('Discounts given'),
			'value': discounts,
			'previous_value': None,
			'change': None,
			'change_percent': None,
			'is_currency': True,
			'is_positive': True,
			'caption': _('Line discounts on invoiced items'),
		},
		{
			'label': _('Taxes / charges'),
			'value': debits,
			'previous_value': None,
			'change': None,
			'change_percent': None,
			'is_currency': True,
			'is_positive': True,
			'caption': _('Invoice debit adjustments in the period'),
		},
		{
			'label': _('Completed deliveries'),
			'value': int(len(deliveries)),
			'previous_value': int(comparison_close.get('deliveries_count', 0)),
			'change': int(len(deliveries)) - int(comparison_close.get('deliveries_count', 0)),
			'change_percent': _safe_pct(
				Decimal(len(deliveries)) - Decimal(comparison_close.get('deliveries_count', 0)),
				Decimal(comparison_close.get('deliveries_count', 0)),
			),
			'is_currency': False,
			'is_positive': True,
			'caption': _('Route deliveries completed in range'),
		},
	]
	return cards


def enrich_product_rows_with_margin(invoice_items):
	rows = {}
	for item in invoice_items:
		name = item.producto_nombre or _('Unnamed product')
		presentacion = getattr(item, 'presentacion', None)
		cost = getattr(presentacion, 'costo', None) if presentacion is not None else None
		qty = int(item.cantidad_facturada or 0)
		revenue = _as_money(item.subtotal)
		line_profit = calculate_profit_from_revenue(
			cost_per_unit=cost,
			quantity=qty,
			revenue=revenue,
		)
		rows.setdefault(
			name,
			{
				'name': name,
				'units_sold': 0,
				'revenue': DECIMAL_ZERO,
				'cogs': DECIMAL_ZERO,
				'profit': DECIMAL_ZERO,
				'invoices_count': 0,
				'presentacion_id': getattr(presentacion, 'id', None),
			},
		)
		rows[name]['units_sold'] += qty
		rows[name]['revenue'] += line_profit['revenue']
		rows[name]['cogs'] += line_profit['cogs']
		rows[name]['profit'] += line_profit['profit_amount']
		rows[name]['invoices_count'] += 1

	ordered = list(rows.values())
	for row in ordered:
		row['margin_percent'] = _safe_pct(row['profit'], row['revenue'])
	ordered.sort(key=lambda row: (row['units_sold'], row['revenue']), reverse=True)
	return ordered


def build_product_rankings(product_rows):
	by_revenue = sorted(product_rows, key=lambda row: row['revenue'], reverse=True)
	by_profit = sorted(product_rows, key=lambda row: row['profit'], reverse=True)
	by_margin = sorted(
		[row for row in product_rows if row.get('margin_percent') is not None],
		key=lambda row: row['margin_percent'],
		reverse=True,
	)
	by_units = sorted(product_rows, key=lambda row: row['units_sold'], reverse=True)
	return {
		'top_units': by_units[:10],
		'bottom_units': list(reversed(by_units[-10:])) if by_units else [],
		'top_revenue': by_revenue[:10],
		'bottom_revenue': list(reversed(by_revenue[-10:])) if by_revenue else [],
		'top_profit': by_profit[:10],
		'bottom_profit': list(reversed(by_profit[-10:])) if by_profit else [],
		'top_margin': by_margin[:10],
		'bottom_margin': list(reversed(by_margin[-10:])) if by_margin else [],
	}


def enrich_customer_rows(invoices):
	rows = {}
	for invoice in invoices:
		customer = invoice.cliente
		if customer is None:
			continue
		key = customer.id
		created_at = getattr(invoice, 'creada_en', None)
		rows.setdefault(
			key,
			{
				'id': key,
				'name': customer.nombre_empresa,
				'invoices_count': 0,
				'sales_amount': DECIMAL_ZERO,
				'collected_amount': DECIMAL_ZERO,
				'pending_amount': DECIMAL_ZERO,
				'first_purchase': created_at,
				'last_purchase': created_at,
				'avg_ticket': DECIMAL_ZERO,
			},
		)
		rows[key]['invoices_count'] += 1
		rows[key]['sales_amount'] += _as_money(invoice.total_neto)
		rows[key]['collected_amount'] += _as_money(invoice.total_neto) - _as_money(invoice.saldo_cliente)
		rows[key]['pending_amount'] += _as_money(invoice.saldo_cliente)
		if created_at:
			if rows[key]['first_purchase'] is None or created_at < rows[key]['first_purchase']:
				rows[key]['first_purchase'] = created_at
			if rows[key]['last_purchase'] is None or created_at > rows[key]['last_purchase']:
				rows[key]['last_purchase'] = created_at

	ordered = list(rows.values())
	for row in ordered:
		row['avg_ticket'] = (row['sales_amount'] / row['invoices_count']) if row['invoices_count'] else DECIMAL_ZERO
	ordered.sort(key=lambda row: (row['sales_amount'], row['invoices_count']), reverse=True)
	return ordered


def build_customer_rankings(customer_rows):
	by_sales = sorted(customer_rows, key=lambda row: row['sales_amount'], reverse=True)
	by_orders = sorted(customer_rows, key=lambda row: row['invoices_count'], reverse=True)
	by_debt = sorted(customer_rows, key=lambda row: row['pending_amount'], reverse=True)
	by_avg = sorted(customer_rows, key=lambda row: row['avg_ticket'], reverse=True)
	return {
		'top_sales': by_sales[:10],
		'bottom_sales': list(reversed(by_sales[-10:])) if by_sales else [],
		'top_orders': by_orders[:10],
		'top_debt': [row for row in by_debt if row['pending_amount'] > 0][:10],
		'top_avg_ticket': by_avg[:10],
	}


def build_inventory_snapshot(*, low_threshold=LOW_STOCK_THRESHOLD):
	stocks = list(
		StockPresentacion.objects.select_related('presentacion__producto', 'presentacion__producto__marca')
		.filter(presentacion__producto__activo=True)
		.order_by('stock_disponible', 'presentacion__producto__nombre')
	)
	out_of_stock = []
	low_stock = []
	overstock = []
	inventory_value = DECIMAL_ZERO
	for stock in stocks:
		presentacion = stock.presentacion
		producto = presentacion.producto
		available = int(stock.stock_disponible or 0)
		cost = _as_money(getattr(presentacion, 'costo', 0))
		line_value = cost * Decimal(available)
		inventory_value += line_value
		row = {
			'name': producto.nombre,
			'presentation': presentacion.nombre,
			'available': available,
			'physical': int(stock.stock_fisico or 0),
			'reserved': int(stock.stock_reservado or 0),
			'value': line_value,
			'brand': getattr(getattr(producto, 'marca', None), 'nombre', '') or '—',
		}
		if available <= 0:
			out_of_stock.append(row)
		elif available <= low_threshold:
			low_stock.append(row)
		elif available >= max(low_threshold * 20, 100):
			overstock.append(row)

	return {
		'out_of_stock': out_of_stock[:25],
		'low_stock': low_stock[:25],
		'overstock': overstock[:25],
		'out_of_stock_count': len(out_of_stock),
		'low_stock_count': len(low_stock),
		'overstock_count': len(overstock),
		'inventory_value': inventory_value,
		'sku_count': len(stocks),
	}


def build_new_vs_returning_customers(*, invoices, period_start):
	customer_ids = {invoice.cliente_id for invoice in invoices if invoice.cliente_id}
	if not customer_ids:
		return {'new_customers': 0, 'returning_customers': 0}

	# Customers whose first approved/created activity is within the period are "new" for BI purposes.
	new_ids = set(
		Cliente.objects.filter(id__in=customer_ids)
		.filter(Q(aprobado_en__gte=period_start) | Q(aprobado_en__isnull=True, usuario__date_joined__gte=period_start))
		.values_list('id', flat=True)
	)
	# Fallback: if aprobado_en missing, treat as returning when they appear in invoices only.
	returning = len(customer_ids - set(new_ids))
	return {
		'new_customers': len(new_ids),
		'returning_customers': returning,
	}


def build_smart_question_presets():
	"""GET shortcuts that map natural questions to filtered report views."""
	return [
		{
			'label': _('What sold most this month?'),
			'query': 'period=month&focus=products',
			'description': _('Top products by units and revenue'),
		},
		{
			'label': _('Who is our best customer?'),
			'query': 'period=month&focus=customers',
			'description': _('Customers ranked by sales'),
		},
		{
			'label': _('Which seller sold most?'),
			'query': 'period=month&focus=vendors',
			'description': _('Sales reps comparison'),
		},
		{
			'label': _('What is out of stock?'),
			'query': 'period=today&focus=inventory',
			'description': _('Inventory health snapshot'),
		},
		{
			'label': _('How much did we sell yesterday?'),
			'query': 'period=today&focus=summary',
			'description': _('Today KPIs with vs previous period'),
		},
		{
			'label': _('Who owes the most?'),
			'query': 'period=month&focus=customers&sort=debt',
			'description': _('Customers with highest open balance'),
		},
		{
			'label': _('Driver collections this week'),
			'query': 'period=week&focus=drivers',
			'description': _('Route close and driver performance'),
		},
		{
			'label': _('Best margin products'),
			'query': 'period=month&focus=margins',
			'description': _('Products with highest profit margin'),
		},
	]


def parse_focus(request):
	data = request.POST if request.method == 'POST' else request.GET
	focus = (data.get('focus') or 'summary').strip().lower()
	allowed = {
		'summary',
		'products',
		'customers',
		'vendors',
		'drivers',
		'finance',
		'inventory',
		'margins',
		'trends',
	}
	if focus not in allowed:
		focus = 'summary'
	return focus


def build_brand_rows(invoice_items):
	rows = {}
	for item in invoice_items:
		presentacion = getattr(item, 'presentacion', None)
		producto = getattr(presentacion, 'producto', None) if presentacion is not None else None
		marca = getattr(producto, 'marca', None) if producto is not None else None
		name = getattr(marca, 'nombre', None) or _('Unbranded')
		rows.setdefault(
			name,
			{'name': name, 'units_sold': 0, 'revenue': DECIMAL_ZERO, 'profit': DECIMAL_ZERO},
		)
		qty = int(item.cantidad_facturada or 0)
		revenue = _as_money(item.subtotal)
		cost = getattr(presentacion, 'costo', None) if presentacion is not None else None
		line_profit = calculate_profit_from_revenue(
			cost_per_unit=cost,
			quantity=qty,
			revenue=revenue,
		)
		rows[name]['units_sold'] += qty
		rows[name]['revenue'] += line_profit['revenue']
		rows[name]['profit'] += line_profit['profit_amount']
	ordered = sorted(rows.values(), key=lambda row: row['revenue'], reverse=True)
	for row in ordered:
		row['margin_percent'] = _safe_pct(row['profit'], row['revenue'])
	return ordered


def build_never_sold_products(*, sold_names, limit=25):
	"""Active catalog products with no invoiced sales in the filtered period."""
	sold = {name.lower() for name in sold_names}
	never_sold = []
	presentaciones = (
		Presentacion.objects.select_related('producto', 'producto__marca')
		.filter(producto__activo=True)
		.order_by('producto__nombre')[:400]
	)
	for presentacion in presentaciones:
		name = presentacion.producto.nombre
		if name.lower() in sold:
			continue
		never_sold.append(
			{
				'name': name,
				'presentation': presentacion.nombre,
				'brand': getattr(getattr(presentacion.producto, 'marca', None), 'nombre', '') or '—',
			}
		)
		if len(never_sold) >= limit:
			break
	return never_sold


def build_period_sales_strip(*, invoice_model):
	"""Always-on sales snapshots for day / week / month / year (local dates)."""
	today = timezone.localdate()
	timezone_info = timezone.get_current_timezone()
	from datetime import datetime, timedelta

	def _range_sum(start_date, end_date):
		start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), timezone_info)
		end_dt = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), datetime.min.time()), timezone_info)
		total = (
			invoice_model.objects.filter(estado='GENERADA', creada_en__gte=start_dt, creada_en__lt=end_dt).aggregate(
				total=Sum('total_neto')
			)['total']
		)
		return _as_money(total)

	week_start = today - timedelta(days=today.weekday())
	month_start = today.replace(day=1)
	year_start = today.replace(month=1, day=1)
	return [
		{'key': 'day', 'label': _('Sales today'), 'value': _range_sum(today, today)},
		{'key': 'week', 'label': _('Sales this week'), 'value': _range_sum(week_start, today)},
		{'key': 'month', 'label': _('Sales this month'), 'value': _range_sum(month_start, today)},
		{'key': 'year', 'label': _('Sales this year'), 'value': _range_sum(year_start, today)},
	]


def append_inventory_kpi_cards(cards, inventory, customer_mix):
	cards.extend(
		[
			{
				'label': _('Out of stock SKUs'),
				'value': int(inventory.get('out_of_stock_count', 0)),
				'previous_value': None,
				'change': None,
				'change_percent': None,
				'is_currency': False,
				'is_positive': inventory.get('out_of_stock_count', 0) == 0,
				'caption': _('Active presentations with zero available stock'),
			},
			{
				'label': _('Low stock SKUs'),
				'value': int(inventory.get('low_stock_count', 0)),
				'previous_value': None,
				'change': None,
				'change_percent': None,
				'is_currency': False,
				'is_positive': inventory.get('low_stock_count', 0) == 0,
				'caption': _('At or below replenishment threshold'),
			},
			{
				'label': _('Inventory value'),
				'value': inventory.get('inventory_value', DECIMAL_ZERO),
				'previous_value': None,
				'change': None,
				'change_percent': None,
				'is_currency': True,
				'is_positive': True,
				'caption': _('Available stock × catalog cost'),
			},
			{
				'label': _('New customers'),
				'value': int(customer_mix.get('new_customers', 0)),
				'previous_value': None,
				'change': None,
				'change_percent': None,
				'is_currency': False,
				'is_positive': True,
				'caption': _('Approved/joined within selected period'),
			},
			{
				'label': _('Returning customers'),
				'value': int(customer_mix.get('returning_customers', 0)),
				'previous_value': None,
				'change': None,
				'change_percent': None,
				'is_currency': False,
				'is_positive': True,
				'caption': _('Previously known customers with invoices'),
			},
		]
	)
	return cards
