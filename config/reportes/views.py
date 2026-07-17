from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape
from io import BytesIO, StringIO

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from config.core.datetime_formats import APP_DATE_STRFTIME, format_local_date, format_local_datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.facturacion.models import Delivery, Invoice, InvoiceItem
from config.pedidos.models import Pedido
from config.reportes.bi_metrics import (
	append_inventory_kpi_cards,
	build_bi_kpi_cards,
	build_brand_rows,
	build_customer_rankings,
	build_inventory_snapshot,
	build_never_sold_products,
	build_new_vs_returning_customers,
	build_period_sales_strip,
	build_product_rankings,
	build_smart_question_presets,
	enrich_customer_rows,
	enrich_product_rows_with_margin,
	parse_focus,
)
from config.usuarios.models import Usuario
from config.usuarios.permissions import internal_permission_required


DECIMAL_ZERO = Decimal('0.00')
PAYMENT_METHOD_ORDER = ('CASH', 'CHEQUE', 'TARJETA', 'TRANSFERENCIA', 'ZELLE', 'ACH')
PAYMENT_METHOD_LABELS = {
	'CASH': _('Cash'),
	'CHEQUE': _('Cheque'),
	'TARJETA': _('Card'),
	'TRANSFERENCIA': _('Transfer'),
	'ZELLE': _('Zelle'),
	'ACH': _('ACH'),
}
PERIOD_CHOICES = (
	('today', _('Today')),
	('week', _('This week')),
	('biweekly', _('Last 14 days')),
	('month', _('This month')),
	('year', _('This year')),
	('custom', _('Custom range')),
)
TREND_CHART_WIDTH = Decimal('100')
TREND_CHART_HEIGHT = Decimal('64')
EXPORTABLE_SECTIONS = {
	'all': _('Full report'),
	'summary': _('Summary'),
	'drivers': _('Driver close'),
	'customers': _('Top customers'),
	'categories': _('Categories'),
	'payments': _('Payment methods'),
	'products': _('Products'),
	'margins': _('Margins'),
	'inventory': _('Inventory'),
	'vendors': _('Sales'),
	'users': _('Internal users'),
}
DONUT_CHART_COLORS = (
	'#0f5c91',
	'#2b9f5a',
	'#d58918',
	'#7552cc',
	'#39a7d8',
	'#db5b6b',
	'#8cbf3f',
)


def _as_money(value):
	return value if value is not None else DECIMAL_ZERO


def _sum_money(values):
	total = DECIMAL_ZERO
	for value in values:
		total += _as_money(value)
	return total


def _safe_division(numerator, denominator):
	if not denominator:
		return None
	return round((numerator / denominator) * 100, 1)


def _format_money(value):
	return f'${_as_money(value):,.2f}'


def _format_number(value):
	if isinstance(value, Decimal):
		return f'{value:,.2f}'
	return str(value)


def _parse_range(request):
	data = request.POST if request.method == 'POST' else request.GET
	today = timezone.localdate()
	preset = (data.get('period') or 'today').strip().lower()
	start_date = parse_date(data.get('start_date') or '')
	end_date = parse_date(data.get('end_date') or '')

	if preset == 'custom' and start_date and end_date and start_date <= end_date:
		label = _('Custom range')
	elif preset == 'week':
		start_date = today - timedelta(days=today.weekday())
		end_date = today
		label = _('This week')
	elif preset == 'biweekly':
		start_date = today - timedelta(days=13)
		end_date = today
		label = _('Last 14 days')
	elif preset == 'month':
		start_date = today.replace(day=1)
		end_date = today
		label = _('This month')
	elif preset == 'year':
		start_date = today.replace(month=1, day=1)
		end_date = today
		label = _('This year')
	else:
		preset = 'today'
		start_date = today
		end_date = today
		label = _('Today')

	timezone_info = timezone.get_current_timezone()
	start_datetime = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), timezone_info)
	end_datetime = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), datetime.min.time()), timezone_info)
	days = (end_date - start_date).days + 1
	comparison_end = start_date - timedelta(days=1)
	comparison_start = comparison_end - timedelta(days=days - 1)
	comparison_start_datetime = timezone.make_aware(datetime.combine(comparison_start, datetime.min.time()), timezone_info)
	comparison_end_datetime = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), timezone_info)

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


def _parse_int(value):
	try:
		parsed = int(value)
		return parsed if parsed > 0 else None
	except (TypeError, ValueError):
		return None


def _parse_filters(request):
	data = request.POST if request.method == 'POST' else request.GET
	section = (data.get('section') or 'all').strip().lower()
	if section not in EXPORTABLE_SECTIONS:
		section = 'all'
	return {
		'driver_id': _parse_int(data.get('driver_id')),
		'vendor_id': _parse_int(data.get('vendor_id')),
		'customer_id': _parse_int(data.get('customer_id')),
		'section': section,
	}


def _payment_totals_for_delivery(delivery):
	totals = {method: DECIMAL_ZERO for method in PAYMENT_METHOD_ORDER}
	payments = list(delivery.payments.all()) if hasattr(delivery, 'payments') else []
	if payments:
		for payment in payments:
			if payment.metodo_pago in totals:
				totals[payment.metodo_pago] += _as_money(payment.monto)
		return totals

	method = (delivery.metodo_pago or '').strip()
	amount = _as_money(delivery.monto_pagado)
	if method == 'MIXTO':
		totals['CASH'] += _as_money(delivery.monto_pagado_cash)
		totals['CHEQUE'] += _as_money(delivery.monto_pagado_cheque)
	elif method == 'CHEQUE':
		totals['CHEQUE'] += _as_money(delivery.monto_pagado_cheque or amount)
	elif method in totals:
		totals[method] += amount
	return totals


def _build_close_snapshot(deliveries):
	payment_totals = {method: DECIMAL_ZERO for method in PAYMENT_METHOD_ORDER}
	delivered_amount = DECIMAL_ZERO
	collected_amount = DECIMAL_ZERO
	outstanding_amount = DECIMAL_ZERO
	items_delivered = 0
	paid_count = 0
	unpaid_count = 0
	blocked_count = 0
	drivers = set()

	for delivery in deliveries:
		invoice = delivery.invoice
		delivered_amount += _as_money(invoice.total_neto)
		outstanding_amount += _as_money(invoice.saldo_cliente)
		collected_amount += _as_money(invoice.total_neto) - _as_money(invoice.saldo_cliente)
		items_delivered += sum(item.cantidad_facturada for item in invoice.items.all())
		blocked_count += 1 if delivery.client_blocked_on_delivery else 0
		paid_count += 1 if delivery.estado_pago == 'PAGADO' else 0
		unpaid_count += 1 if delivery.estado_pago == 'NO_PAGADO' else 0
		if delivery.driver_id:
			drivers.add(delivery.driver_id)
		for method, amount in _payment_totals_for_delivery(delivery).items():
			payment_totals[method] += amount

	reconciliation_gap = delivered_amount - (collected_amount + outstanding_amount)
	return {
		'deliveries_count': len(deliveries),
		'drivers_count': len(drivers),
		'items_delivered': items_delivered,
		'delivered_amount': delivered_amount,
		'collected_amount': collected_amount,
		'outstanding_amount': outstanding_amount,
		'paid_count': paid_count,
		'unpaid_count': unpaid_count,
		'blocked_count': blocked_count,
		'reconciliation_gap': reconciliation_gap,
		'payment_totals': payment_totals,
	}


def _build_driver_rows(deliveries):
	rows = {}
	for delivery in deliveries:
		driver = delivery.driver
		key = delivery.driver_id or 0
		name = driver.get_full_name() or driver.username if driver else _('Unassigned')
		if key not in rows:
			rows[key] = {
				'name': name,
				'deliveries_count': 0,
				'paid_count': 0,
				'unpaid_count': 0,
				'delivered_amount': DECIMAL_ZERO,
				'collected_amount': DECIMAL_ZERO,
				'outstanding_amount': DECIMAL_ZERO,
				'blocked_count': 0,
				'payment_totals': {method: DECIMAL_ZERO for method in PAYMENT_METHOD_ORDER},
			}
		row = rows[key]
		invoice = delivery.invoice
		row['deliveries_count'] += 1
		row['paid_count'] += 1 if delivery.estado_pago == 'PAGADO' else 0
		row['unpaid_count'] += 1 if delivery.estado_pago == 'NO_PAGADO' else 0
		row['blocked_count'] += 1 if delivery.client_blocked_on_delivery else 0
		row['delivered_amount'] += _as_money(invoice.total_neto)
		row['outstanding_amount'] += _as_money(invoice.saldo_cliente)
		row['collected_amount'] += _as_money(invoice.total_neto) - _as_money(invoice.saldo_cliente)
		for method, amount in _payment_totals_for_delivery(delivery).items():
			row['payment_totals'][method] += amount

	ordered_rows = list(rows.values())
	ordered_rows.sort(key=lambda row: (row['delivered_amount'], row['deliveries_count']), reverse=True)
	return ordered_rows


def _build_vendor_rows(orders, invoices):
	rows = {}
	for order in orders:
		vendor = getattr(order, 'vendedor', None)
		key = vendor.id if vendor else 0
		name = vendor.get_full_name() or vendor.username if vendor else _('Unassigned')
		rows.setdefault(key, {
			'name': name,
			'orders_count': 0,
			'invoices_count': 0,
			'sales_amount': DECIMAL_ZERO,
			'collected_amount': DECIMAL_ZERO,
			'pending_amount': DECIMAL_ZERO,
		})
		rows[key]['orders_count'] += 1

	for invoice in invoices:
		vendor = getattr(invoice.pedido, 'vendedor', None) if invoice.pedido_id else None
		key = vendor.id if vendor else 0
		name = vendor.get_full_name() or vendor.username if vendor else _('Unassigned')
		rows.setdefault(key, {
			'name': name,
			'orders_count': 0,
			'invoices_count': 0,
			'sales_amount': DECIMAL_ZERO,
			'collected_amount': DECIMAL_ZERO,
			'pending_amount': DECIMAL_ZERO,
		})
		rows[key]['invoices_count'] += 1
		rows[key]['sales_amount'] += _as_money(invoice.total_neto)
		rows[key]['collected_amount'] += _as_money(invoice.total_neto) - _as_money(invoice.saldo_cliente)
		rows[key]['pending_amount'] += _as_money(invoice.saldo_cliente)

	ordered_rows = list(rows.values())
	for row in ordered_rows:
		row['avg_ticket'] = row['sales_amount'] / row['invoices_count'] if row['invoices_count'] else DECIMAL_ZERO
	ordered_rows.sort(key=lambda row: (row['sales_amount'], row['orders_count']), reverse=True)
	return ordered_rows


def _build_creator_rows(invoices):
	rows = {}
	for invoice in invoices:
		creator = getattr(invoice, 'creada_por', None)
		key = creator.id if creator else 0
		name = creator.get_full_name() or creator.username if creator else _('System / unassigned')
		rows.setdefault(key, {
			'name': name,
			'invoices_count': 0,
			'total_amount': DECIMAL_ZERO,
			'collected_amount': DECIMAL_ZERO,
			'pending_amount': DECIMAL_ZERO,
		})
		rows[key]['invoices_count'] += 1
		rows[key]['total_amount'] += _as_money(invoice.total_neto)
		rows[key]['collected_amount'] += _as_money(invoice.total_neto) - _as_money(invoice.saldo_cliente)
		rows[key]['pending_amount'] += _as_money(invoice.saldo_cliente)

	ordered_rows = list(rows.values())
	ordered_rows.sort(key=lambda row: (row['total_amount'], row['invoices_count']), reverse=True)
	return ordered_rows


def _build_product_rows(invoice_items):
	rows = {}
	for item in invoice_items:
		name = item.producto_nombre or _('Unnamed product')
		rows.setdefault(name, {
			'name': name,
			'units_sold': 0,
			'revenue': DECIMAL_ZERO,
			'invoices_count': 0,
		})
		rows[name]['units_sold'] += int(item.cantidad_facturada or 0)
		rows[name]['revenue'] += _as_money(item.subtotal)
		rows[name]['invoices_count'] += 1

	ordered_rows = list(rows.values())
	ordered_rows.sort(key=lambda row: (row['units_sold'], row['revenue']), reverse=True)
	return ordered_rows


def _build_customer_rows(invoices):
	rows = {}
	for invoice in invoices:
		customer = invoice.cliente
		key = customer.id
		rows.setdefault(key, {
			'name': customer.nombre_empresa,
			'invoices_count': 0,
			'sales_amount': DECIMAL_ZERO,
			'collected_amount': DECIMAL_ZERO,
			'pending_amount': DECIMAL_ZERO,
		})
		rows[key]['invoices_count'] += 1
		rows[key]['sales_amount'] += _as_money(invoice.total_neto)
		rows[key]['collected_amount'] += _as_money(invoice.total_neto) - _as_money(invoice.saldo_cliente)
		rows[key]['pending_amount'] += _as_money(invoice.saldo_cliente)

	ordered_rows = list(rows.values())
	ordered_rows.sort(key=lambda row: (row['sales_amount'], row['invoices_count']), reverse=True)
	return ordered_rows


def _build_category_rows(invoice_items):
	rows = {}
	for item in invoice_items:
		category_name = _('Uncategorized')
		if item.presentacion_id and getattr(item.presentacion, 'producto', None) and getattr(item.presentacion.producto, 'categoria', None):
			category_name = item.presentacion.producto.categoria.nombre
		rows.setdefault(category_name, {
			'name': category_name,
			'units_sold': 0,
			'revenue': DECIMAL_ZERO,
			'invoices_count': 0,
		})
		rows[category_name]['units_sold'] += int(item.cantidad_facturada or 0)
		rows[category_name]['revenue'] += _as_money(item.subtotal)
		rows[category_name]['invoices_count'] += 1

	ordered_rows = list(rows.values())
	ordered_rows.sort(key=lambda row: (row['revenue'], row['units_sold']), reverse=True)
	return ordered_rows


def _build_payment_method_rows(deliveries):
	rows = {method: {'label': PAYMENT_METHOD_LABELS[method], 'amount': DECIMAL_ZERO, 'deliveries_count': 0} for method in PAYMENT_METHOD_ORDER}
	for delivery in deliveries:
		for method, amount in _payment_totals_for_delivery(delivery).items():
			if amount > 0:
				rows[method]['amount'] += amount
				rows[method]['deliveries_count'] += 1
	ordered_rows = list(rows.values())
	total_amount = _sum_money(row['amount'] for row in ordered_rows)
	max_amount = max((row['amount'] for row in ordered_rows), default=DECIMAL_ZERO)
	for row in ordered_rows:
		row['share_percent'] = _safe_division(row['amount'], total_amount) or 0
		row['bar_width'] = 0 if max_amount == DECIMAL_ZERO else float((row['amount'] / max_amount) * Decimal('100'))
	ordered_rows.sort(key=lambda row: (row['amount'], row['deliveries_count']), reverse=True)
	return ordered_rows


def _add_bar_metadata(rows, value_key):
	max_value = max((row[value_key] for row in rows), default=DECIMAL_ZERO)
	for row in rows:
		row['bar_width'] = 0 if max_value == DECIMAL_ZERO else float((row[value_key] / max_value) * Decimal('100'))
	return rows


def _build_donut_chart(rows, *, value_key, label_key='name', colors=DONUT_CHART_COLORS, limit=6):
	selected_rows = rows[:limit]
	total_value = _sum_money(row[value_key] for row in selected_rows)
	if total_value == DECIMAL_ZERO:
		return {
			'style': 'conic-gradient(#e7eef5 0 100%)',
			'segments': [],
			'total_value': DECIMAL_ZERO,
		}

	segments = []
	gradient_stops = []
	current_percent = Decimal('0')
	for index, row in enumerate(selected_rows):
		value = _as_money(row[value_key])
		if value <= DECIMAL_ZERO:
			continue
		share_percent = (value / total_value) * Decimal('100')
		start_percent = current_percent
		current_percent += share_percent
		color = colors[index % len(colors)]
		gradient_stops.append(f'{color} {start_percent:.2f}% {current_percent:.2f}%')
		segments.append({
			'label': row.get(label_key) or row.get('label') or _('Item'),
			'value': value,
			'share_percent': round(share_percent, 1),
			'color': color,
		})

	if current_percent < Decimal('100'):
		gradient_stops.append(f'#e7eef5 {current_percent:.2f}% 100%')

	return {
		'style': f'conic-gradient({", ".join(gradient_stops)})',
		'segments': segments,
		'total_value': total_value,
	}


def _chart_number(value):
	if isinstance(value, Decimal):
		return float(value)
	return value


def _build_trend_chart_payload(trend_rows):
	return {
		'labels': [row['label'] for row in trend_rows],
		'sales': [_chart_number(row['sales_amount']) for row in trend_rows],
		'collected': [_chart_number(row['collected_amount']) for row in trend_rows],
	}


def _build_donut_chart_payload(chart_data):
	return {
		'labels': [segment['label'] for segment in chart_data['segments']],
		'values': [_chart_number(segment['value']) for segment in chart_data['segments']],
		'colors': [segment['color'] for segment in chart_data['segments']],
	}


def _build_driver_chart_payload(driver_rows):
	selected_rows = driver_rows[:8]
	return {
		'labels': [row['name'] for row in selected_rows],
		'delivered': [_chart_number(row['delivered_amount']) for row in selected_rows],
		'collected': [_chart_number(row['collected_amount']) for row in selected_rows],
		'pending': [_chart_number(row['outstanding_amount']) for row in selected_rows],
	}


def _build_recent_close_chart_payload(recent_close_rows):
	return {
		'labels': [f"{row['invoice_number']}" for row in recent_close_rows],
		'collected': [_chart_number(row['collected_amount']) for row in recent_close_rows],
		'pending': [_chart_number(row['outstanding_amount']) for row in recent_close_rows],
	}


def _build_vendor_chart_payload(vendor_rows):
	selected_rows = vendor_rows[:8]
	return {
		'labels': [row['name'] for row in selected_rows],
		'sales': [_chart_number(row['sales_amount']) for row in selected_rows],
		'pending': [_chart_number(row['pending_amount']) for row in selected_rows],
		'avg_ticket': [_chart_number(row['avg_ticket']) for row in selected_rows],
	}


def _build_creator_chart_payload(creator_rows):
	selected_rows = creator_rows[:8]
	return {
		'labels': [row['name'] for row in selected_rows],
		'total': [_chart_number(row['total_amount']) for row in selected_rows],
		'pending': [_chart_number(row['pending_amount']) for row in selected_rows],
		'invoices': [row['invoices_count'] for row in selected_rows],
	}


def _build_low_rotation_chart_payload(low_products):
	selected_rows = low_products[:6]
	return {
		'labels': [row['name'] for row in selected_rows],
		'units': [row['units_sold'] for row in selected_rows],
		'revenue': [_chart_number(row['revenue']) for row in selected_rows],
	}


def _build_trend_rows(period, orders, invoices, deliveries):
	buckets = defaultdict(lambda: {
		'label': '',
		'orders_count': 0,
		'invoices_count': 0,
		'sales_amount': DECIMAL_ZERO,
		'collected_amount': DECIMAL_ZERO,
	})
	current = period['start_date']
	while current <= period['end_date']:
		buckets[current]['label'] = current.strftime(APP_DATE_STRFTIME)
		current += timedelta(days=1)

	for order in orders:
		day = timezone.localtime(order.creada_en).date()
		if day in buckets:
			buckets[day]['orders_count'] += 1

	for invoice in invoices:
		day = timezone.localtime(invoice.creada_en).date()
		if day in buckets:
			buckets[day]['invoices_count'] += 1
			buckets[day]['sales_amount'] += _as_money(invoice.total_neto)

	for delivery in deliveries:
		day = timezone.localtime(delivery.delivered_at).date() if delivery.delivered_at else None
		if day in buckets:
			buckets[day]['collected_amount'] += _as_money(delivery.invoice.total_neto) - _as_money(delivery.invoice.saldo_cliente)

	rows = []
	max_sales = max((bucket['sales_amount'] for bucket in buckets.values()), default=DECIMAL_ZERO)
	max_collected = max((bucket['collected_amount'] for bucket in buckets.values()), default=DECIMAL_ZERO)
	for index, day in enumerate(sorted(buckets.keys())):
		row = buckets[day]
		x = Decimal('0') if period['days'] <= 1 else (Decimal(index) / Decimal(period['days'] - 1)) * TREND_CHART_WIDTH
		sales_height = Decimal('0') if max_sales == DECIMAL_ZERO else (row['sales_amount'] / max_sales) * TREND_CHART_HEIGHT
		collected_height = Decimal('0') if max_collected == DECIMAL_ZERO else (row['collected_amount'] / max_collected) * TREND_CHART_HEIGHT
		row['sales_point'] = f"{x:.2f},{(TREND_CHART_HEIGHT - sales_height):.2f}"
		row['collected_point'] = f"{x:.2f},{(TREND_CHART_HEIGHT - collected_height):.2f}"
		row['sales_bar_height'] = float(sales_height)
		row['collected_bar_height'] = float(collected_height)
		rows.append(row)
	return rows


def _build_trend_chart(trend_rows):
	if not trend_rows:
		return {
			'sales_points': '',
			'collected_points': '',
		}
	return {
		'sales_points': ' '.join(row['sales_point'] for row in trend_rows),
		'collected_points': ' '.join(row['collected_point'] for row in trend_rows),
	}


def _build_export_querystring(request, period):
	querystring = request.GET.urlencode()
	if querystring:
		return querystring
	return f'period={period["preset"]}'


def _build_export_sections(report_data, section='all'):
	product_export_rows = report_data.get('product_rows_full') or report_data.get('top_products') or []
	customer_export_rows = report_data.get('customer_rows_full') or report_data.get('customer_rows') or []
	sections = [
		{
			'key': 'summary',
			'title': _('Executive summary'),
			'headers': [_('Metric'), _('Value'), _('Change %')],
			'rows': [
				[
					card['label'],
					_format_money(card['value']) if card['is_currency'] else card['value'],
					f"{card['change_percent']}%" if card.get('change_percent') is not None else '—',
				]
				for card in report_data['summary_cards']
			],
		},
		{
			'key': 'drivers',
			'title': _('Driver close'),
			'headers': [_('Driver'), _('Deliveries'), _('Delivered'), _('Collected'), _('Outstanding')],
			'rows': [
				[row['name'], row['deliveries_count'], _format_money(row['delivered_amount']), _format_money(row['collected_amount']), _format_money(row['outstanding_amount'])]
				for row in report_data['driver_rows']
			],
		},
		{
			'key': 'customers',
			'title': _('Customers'),
			'headers': [_('Customer'), _('Invoices'), _('Sales'), _('Collected'), _('Pending'), _('Avg ticket')],
			'rows': [
				[
					row['name'],
					row['invoices_count'],
					_format_money(row['sales_amount']),
					_format_money(row['collected_amount']),
					_format_money(row['pending_amount']),
					_format_money(row.get('avg_ticket', 0)),
				]
				for row in customer_export_rows
			],
		},
		{
			'key': 'categories',
			'title': _('Categories'),
			'headers': [_('Category'), _('Units'), _('Revenue')],
			'rows': [[row['name'], row['units_sold'], _format_money(row['revenue'])] for row in report_data['category_rows']],
		},
		{
			'key': 'payments',
			'title': _('Payment methods'),
			'headers': [_('Method'), _('Amount'), _('Deliveries')],
			'rows': [[row['label'], _format_money(row['amount']), row['deliveries_count']] for row in report_data['payment_method_rows']],
		},
		{
			'key': 'products',
			'title': _('Products'),
			'headers': [_('Product'), _('Units'), _('Revenue'), _('Profit'), _('Margin %')],
			'rows': [
				[
					row['name'],
					row['units_sold'],
					_format_money(row['revenue']),
					_format_money(row.get('profit', 0)),
					f"{row['margin_percent']}%" if row.get('margin_percent') is not None else '—',
				]
				for row in product_export_rows
			],
		},
		{
			'key': 'margins',
			'title': _('Best margins'),
			'headers': [_('Product'), _('Margin %'), _('Profit'), _('Revenue')],
			'rows': [
				[
					row['name'],
					f"{row['margin_percent']}%" if row.get('margin_percent') is not None else '—',
					_format_money(row.get('profit', 0)),
					_format_money(row['revenue']),
				]
				for row in report_data.get('product_rankings', {}).get('top_margin', [])
			],
		},
		{
			'key': 'inventory',
			'title': _('Inventory alerts'),
			'headers': [_('Product'), _('Presentation'), _('Available'), _('Value')],
			'rows': [
				[row['name'], row['presentation'], row['available'], _format_money(row['value'])]
				for row in (
					report_data.get('inventory', {}).get('out_of_stock', [])
					+ report_data.get('inventory', {}).get('low_stock', [])
				)
			],
		},
		{
			'key': 'vendors',
			'title': _('Sales reps'),
			'headers': [_('Sales rep'), _('Orders'), _('Invoices'), _('Sales'), _('Avg ticket'), _('Pending')],
			'rows': [
				[row['name'], row['orders_count'], row['invoices_count'], _format_money(row['sales_amount']), _format_money(row['avg_ticket']), _format_money(row['pending_amount'])]
				for row in report_data['vendor_rows']
			],
		},
		{
			'key': 'users',
			'title': _('Internal users'),
			'headers': [_('User'), _('Invoices'), _('Total'), _('Collected'), _('Pending')],
			'rows': [
				[row['name'], row['invoices_count'], _format_money(row['total_amount']), _format_money(row['collected_amount']), _format_money(row['pending_amount'])]
				for row in report_data['creator_rows']
			],
		},
	]
	if section == 'all':
		return sections
	return [item for item in sections if item['key'] == section]


def _filter_export_sections(section_items):
	return [item for item in section_items if item['rows']]


def _build_summary_cards(orders, invoices, deliveries, close_snapshot):
	gross_sales = _sum_money(invoice.total_neto for invoice in invoices)
	pending_balance = _sum_money(invoice.saldo_cliente for invoice in invoices)
	credit_sales = sum(1 for invoice in invoices if _as_money(invoice.saldo_cliente) > DECIMAL_ZERO)
	return [
		{
			'label': _('Orders received'),
			'value': len(orders),
			'is_currency': False,
			'caption': _('New sales orders registered in the selected range.'),
		},
		{
			'label': _('Invoices generated'),
			'value': len(invoices),
			'is_currency': False,
			'caption': _('Invoices issued and ready to reconcile.'),
		},
		{
			'label': _('Gross sales'),
			'value': gross_sales,
			'is_currency': True,
			'caption': _('Net amount invoiced in the selected period.'),
		},
		{
			'label': _('Collected in routes'),
			'value': close_snapshot['collected_amount'],
			'is_currency': True,
			'caption': _('Money collected by drivers on completed deliveries.'),
		},
		{
			'label': _('Pending balance'),
			'value': pending_balance,
			'is_currency': True,
			'caption': _('Outstanding customer balance still open.'),
		},
		{
			'label': _('Credit-risk deliveries'),
			'value': credit_sales,
			'is_currency': False,
			'caption': _('Invoices that still carry an open balance.'),
		},
	]


def _build_payment_rows(payment_totals):
	return [
		{
			'code': method,
			'label': PAYMENT_METHOD_LABELS[method],
			'value': payment_totals.get(method, DECIMAL_ZERO),
		}
		for method in PAYMENT_METHOD_ORDER
	]


def _build_recent_close_rows(deliveries):
	rows = []
	for delivery in deliveries[:8]:
		invoice = delivery.invoice
		driver = delivery.driver
		rows.append({
			'invoice_number': invoice.numero,
			'customer_name': invoice.cliente.nombre_empresa,
			'driver_name': driver.get_full_name() or driver.username if driver else _('Unassigned'),
			'delivery_status': delivery.get_estado_display(),
			'payment_status': delivery.get_estado_pago_display(),
			'delivered_amount': invoice.total_neto,
			'collected_amount': _as_money(invoice.total_neto) - _as_money(invoice.saldo_cliente),
			'outstanding_amount': invoice.saldo_cliente,
			'delivered_at': delivery.delivered_at,
		})
	return rows


def _build_comparison_rows(current_orders, current_invoices, current_close, previous_orders, previous_invoices, previous_close):
	metrics = [
		(_('Orders received'), Decimal(len(current_orders)), Decimal(len(previous_orders))),
		(_('Invoices generated'), Decimal(len(current_invoices)), Decimal(len(previous_invoices))),
		(_('Gross sales'), _sum_money(invoice.total_neto for invoice in current_invoices), _sum_money(invoice.total_neto for invoice in previous_invoices)),
		(_('Collected amount'), current_close['collected_amount'], previous_close['collected_amount']),
		(_('Open balance'), _sum_money(invoice.saldo_cliente for invoice in current_invoices), _sum_money(invoice.saldo_cliente for invoice in previous_invoices)),
	]
	rows = []
	for label, current_value, previous_value in metrics:
		change = current_value - previous_value
		rows.append({
			'label': label,
			'current_value': current_value,
			'previous_value': previous_value,
			'change': change,
			'change_percent': _safe_division(change, previous_value),
			'is_positive': change >= 0,
		})
	return rows


def _build_focus_alerts(close_snapshot, top_products, low_products, driver_rows):
	alerts = []
	if close_snapshot['unpaid_count']:
		alerts.append({
			'title': _('Unpaid deliveries require review'),
			'description': _('There are %(count)s completed deliveries without payment recorded in the current period.') % {'count': close_snapshot['unpaid_count']},
		})
	if close_snapshot['reconciliation_gap'] != DECIMAL_ZERO:
		alerts.append({
			'title': _('Reconciliation mismatch detected'),
			'description': _('Delivered amount and collected plus outstanding balance do not fully match. Review route payments and open balances.'),
		})
	if top_products:
		alerts.append({
			'title': _('Top product in the selected period'),
			'description': _('%(name)s leads with %(units)s units and %(amount)s in revenue.') % {
				'name': top_products[0]['name'],
				'units': top_products[0]['units_sold'],
				'amount': top_products[0]['revenue'],
			},
		})
	if low_products:
		alerts.append({
			'title': _('Low seller to watch'),
			'description': _('%(name)s closed the period at %(units)s units. It may need follow-up or promotion.') % {
				'name': low_products[0]['name'],
				'units': low_products[0]['units_sold'],
			},
		})
	if driver_rows:
		alerts.append({
			'title': _('Driver with highest route volume'),
			'description': _('%(name)s handled %(count)s deliveries and %(amount)s in delivered value.') % {
				'name': driver_rows[0]['name'],
				'count': driver_rows[0]['deliveries_count'],
				'amount': driver_rows[0]['delivered_amount'],
			},
		})
	return alerts


def _build_email_subject(*, period, section_label):
	return _('%(section)s report | %(start)s - %(end)s') % {
		'section': section_label,
		'start': format_local_date(period['start_date']),
		'end': format_local_date(period['end_date']),
	}


def _build_email_body(*, period, report_data, section_items):
	lines = [
		str(_('Reports Center')),
		f"{period['label']}: {format_local_date(period['start_date'])} - {format_local_date(period['end_date'])}",
		'',
	]
	for card in report_data['summary_cards'][:4]:
		value = _format_money(card['value']) if card['is_currency'] else card['value']
		lines.append(f"- {card['label']}: {value}")
	lines.append('')
	for section in section_items:
		lines.append(str(section['title']))
		for row in section['rows'][:5]:
			lines.append(' | '.join(str(value) for value in row))
		lines.append('')
	if report_data.get('focus_alerts'):
		lines.append(str(_('Operational alerts')))
		for alert in report_data['focus_alerts'][:4]:
			lines.append(f"- {alert['title']}: {alert['description']}")
	return '\n'.join(lines)


def _build_email_recipients(explicit_emails=None):
	if explicit_emails:
		return sorted({email.strip() for email in explicit_emails if email and email.strip()})
	recipients = set()
	for user in Usuario.objects.filter(is_active=True).exclude(email=''):
		if user.is_superuser or user.role == 'admin' or user.has_internal_permission('backoffice.reports.view'):
			recipients.add(user.email.strip())
	fallback_email = (getattr(settings, 'ORDERS_NOTIFICATION_EMAIL', '') or '').strip()
	if fallback_email:
		recipients.add(fallback_email)
	return sorted(recipients)


def send_reports_email(*, period, report_data, section='all', recipient_emails=None):
	section_items = _filter_export_sections(_build_export_sections(report_data, section=section))
	recipients = _build_email_recipients(recipient_emails)
	if not recipients:
		return []
	section_label = EXPORTABLE_SECTIONS.get(section, EXPORTABLE_SECTIONS['all'])
	subject = _build_email_subject(period=period, section_label=section_label)
	body = _build_email_body(period=period, report_data=report_data, section_items=section_items)
	pdf_response = _build_pdf_response(period=period, report_data=report_data, section=section)
	email = EmailMultiAlternatives(
		subject=subject,
		body=body,
		from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', '') or getattr(settings, 'SERVER_EMAIL', '') or None,
		to=recipients,
	)
	email.attach(
		f'reports-{section}-{period["start_date"]}-{period["end_date"]}.pdf',
		pdf_response.content,
		'application/pdf',
	)
	email.send(fail_silently=False)
	return recipients


def _build_pdf_response(*, period, report_data, section='all'):
	buffer = BytesIO()
	doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), rightMargin=24, leftMargin=24, topMargin=36, bottomMargin=36)
	styles = getSampleStyleSheet()
	title_style = ParagraphStyle('ReportsTitle', parent=styles['Heading1'], textColor=colors.HexColor('#083b66'), fontSize=20, leading=24)
	subtitle_style = ParagraphStyle('ReportsSubtitle', parent=styles['BodyText'], textColor=colors.HexColor('#50657a'), fontSize=10, leading=13)
	header_style = ParagraphStyle('ReportsHeader', parent=styles['Heading3'], textColor=colors.HexColor('#0f5c91'), fontSize=12, leading=15, spaceAfter=8)
	body_style = styles['BodyText']
	company = report_data.get('company_name') or 'La Tortilla Grocery'
	generated_by = report_data.get('generated_by') or '—'
	story = [
		Paragraph(escape(str(company)), title_style),
		Paragraph(str(_('Business Intelligence Report')), header_style),
		Paragraph(
			f"{period['label']} | {format_local_date(period['start_date'])} - {format_local_date(period['end_date'])} | "
			f"{_('Generated by')}: {escape(str(generated_by))} | {format_local_datetime(timezone.now())}",
			subtitle_style,
		),
		Spacer(1, 0.15 * inch),
		Paragraph(str(_('Executive summary')), header_style),
	]
	for card in report_data.get('summary_cards', [])[:8]:
		value = _format_money(card['value']) if card['is_currency'] else card['value']
		delta = f" ({card['change_percent']}%)" if card.get('change_percent') is not None else ''
		story.append(Paragraph(f"• {escape(str(card['label']))}: {escape(str(value))}{delta}", body_style))
	story.append(Spacer(1, 0.16 * inch))

	for section_item in _filter_export_sections(_build_export_sections(report_data, section=section)):
		story.append(Paragraph(str(section_item['title']), header_style))
		table_data = [section_item['headers']] + [[str(cell) for cell in row] for row in section_item['rows'][:18]]
		table = Table(table_data, repeatRows=1)
		table.setStyle(TableStyle([
			('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f5c91')),
			('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
			('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
			('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d3e4f2')),
			('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7fbff')]),
			('FONTSIZE', (0, 0), (-1, -1), 8),
			('LEADING', (0, 0), (-1, -1), 10),
			('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
			('LEFTPADDING', (0, 0), (-1, -1), 5),
			('RIGHTPADDING', (0, 0), (-1, -1), 5),
		]))
		story.extend([table, Spacer(1, 0.16 * inch)])

	def _add_page_number(canvas, doc_obj):
		canvas.saveState()
		canvas.setFont('Helvetica', 8)
		canvas.setFillColor(colors.HexColor('#50657a'))
		canvas.drawString(24, 18, str(company))
		canvas.drawRightString(landscape(letter)[0] - 24, 18, f"{_('Page')} {doc_obj.page}")
		canvas.restoreState()

	doc.build(story, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
	pdf = buffer.getvalue()
	buffer.close()
	response = HttpResponse(pdf, content_type='application/pdf')
	response['Content-Disposition'] = f'attachment; filename="reports-{period["start_date"]}-{period["end_date"]}.pdf"'
	return response


def _build_excel_response(*, period, report_data, section='all'):
	parts = [
		'<html><head><meta charset="utf-8"></head><body>',
		f'<h1>{escape(str(_("Reports Center")))}</h1>',
		f'<p>{escape(period["label"])} | {escape(format_local_date(period["start_date"]))} - {escape(format_local_date(period["end_date"]))}</p>',
	]
	for section_item in _filter_export_sections(_build_export_sections(report_data, section=section)):
		parts.append(f'<h2>{escape(str(section_item["title"]))}</h2>')
		parts.append('<table border="1" cellspacing="0" cellpadding="4">')
		parts.append('<tr>')
		for header in section_item['headers']:
			parts.append(f'<th style="background:#0f5c91;color:#fff">{escape(str(header))}</th>')
		parts.append('</tr>')
		for row in section_item['rows']:
			parts.append('<tr>')
			for value in row:
				parts.append(f'<td>{escape(_format_number(value))}</td>')
			parts.append('</tr>')
		parts.append('</table><br>')
	parts.append('</body></html>')
	response = HttpResponse(''.join(parts), content_type='application/vnd.ms-excel; charset=utf-8')
	response['Content-Disposition'] = f'attachment; filename="reports-{period["start_date"]}-{period["end_date"]}.xls"'
	return response


def _build_csv_response(*, period, report_data, section='all'):
	import csv

	buffer = StringIO()
	writer = csv.writer(buffer)
	writer.writerow([_('Reports Center'), period['label'], format_local_date(period['start_date']), format_local_date(period['end_date'])])
	writer.writerow([])
	for section_item in _filter_export_sections(_build_export_sections(report_data, section=section)):
		writer.writerow([section_item['title']])
		writer.writerow(section_item['headers'])
		for row in section_item['rows']:
			writer.writerow(row)
		writer.writerow([])
	response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
	response['Content-Disposition'] = f'attachment; filename="reports-{period["start_date"]}-{period["end_date"]}.csv"'
	return response


def _collect_filter_options(raw_data):
	return {
		'drivers': sorted({(delivery.driver_id, delivery.driver.get_full_name() or delivery.driver.username) for delivery in raw_data['deliveries'] if delivery.driver_id}, key=lambda item: item[1].lower()),
		'vendors': sorted({(invoice.pedido.vendedor_id, invoice.pedido.vendedor.get_full_name() or invoice.pedido.vendedor.username) for invoice in raw_data['invoices'] if getattr(invoice.pedido, 'vendedor_id', None)}, key=lambda item: item[1].lower()),
		'customers': sorted({(invoice.cliente_id, invoice.cliente.nombre_empresa) for invoice in raw_data['invoices'] if invoice.cliente_id}, key=lambda item: item[1].lower()),
	}


def _collect_report_data(period, filters=None):
	filters = filters or {}
	completed_statuses = {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}
	orders_queryset = Pedido.objects.select_related('cliente', 'vendedor').filter(
			creada_en__gte=period['start_datetime'],
			creada_en__lt=period['end_datetime'],
		)
	invoices_queryset = Invoice.objects.select_related('cliente', 'pedido__vendedor', 'creada_por').prefetch_related('items').filter(
			estado='GENERADA',
			creada_en__gte=period['start_datetime'],
			creada_en__lt=period['end_datetime'],
		)
	deliveries_queryset = Delivery.objects.select_related('driver', 'invoice__cliente', 'invoice__pedido__vendedor', 'invoice__creada_por').prefetch_related('payments', 'invoice__items').filter(
			estado__in=completed_statuses,
			delivered_at__gte=period['start_datetime'],
			delivered_at__lt=period['end_datetime'],
		)
	comparison_orders_queryset = Pedido.objects.filter(
		creada_en__gte=period['comparison_start_datetime'],
		creada_en__lt=period['comparison_end_datetime'],
	)
	comparison_invoices_queryset = Invoice.objects.filter(
		estado='GENERADA',
		creada_en__gte=period['comparison_start_datetime'],
		creada_en__lt=period['comparison_end_datetime'],
	)
	comparison_deliveries_queryset = Delivery.objects.select_related('invoice').prefetch_related('payments').filter(
		estado__in=completed_statuses,
		delivered_at__gte=period['comparison_start_datetime'],
		delivered_at__lt=period['comparison_end_datetime'],
	)
	if filters.get('customer_id'):
		orders_queryset = orders_queryset.filter(cliente_id=filters['customer_id'])
		invoices_queryset = invoices_queryset.filter(cliente_id=filters['customer_id'])
		deliveries_queryset = deliveries_queryset.filter(invoice__cliente_id=filters['customer_id'])
		comparison_orders_queryset = comparison_orders_queryset.filter(cliente_id=filters['customer_id'])
		comparison_invoices_queryset = comparison_invoices_queryset.filter(cliente_id=filters['customer_id'])
		comparison_deliveries_queryset = comparison_deliveries_queryset.filter(invoice__cliente_id=filters['customer_id'])
	if filters.get('vendor_id'):
		orders_queryset = orders_queryset.filter(vendedor_id=filters['vendor_id'])
		invoices_queryset = invoices_queryset.filter(pedido__vendedor_id=filters['vendor_id'])
		deliveries_queryset = deliveries_queryset.filter(invoice__pedido__vendedor_id=filters['vendor_id'])
		comparison_orders_queryset = comparison_orders_queryset.filter(vendedor_id=filters['vendor_id'])
		comparison_invoices_queryset = comparison_invoices_queryset.filter(pedido__vendedor_id=filters['vendor_id'])
		comparison_deliveries_queryset = comparison_deliveries_queryset.filter(invoice__pedido__vendedor_id=filters['vendor_id'])
	if filters.get('driver_id'):
		invoices_queryset = invoices_queryset.filter(driver_id=filters['driver_id'])
		deliveries_queryset = deliveries_queryset.filter(driver_id=filters['driver_id'])
		comparison_invoices_queryset = comparison_invoices_queryset.filter(driver_id=filters['driver_id'])
		comparison_deliveries_queryset = comparison_deliveries_queryset.filter(driver_id=filters['driver_id'])
	orders = list(orders_queryset.order_by('-creada_en'))
	invoices = list(invoices_queryset.order_by('-creada_en'))
	deliveries = list(deliveries_queryset.order_by('-delivered_at', '-updated_at'))
	invoice_items = list(
		InvoiceItem.objects.select_related(
			'invoice',
			'presentacion__producto__categoria',
			'presentacion__producto__marca',
		).filter(
			invoice__estado='GENERADA',
			invoice_id__in=[invoice.id for invoice in invoices] or [-1],
		).order_by('producto_nombre')
	)
	comparison_orders = list(comparison_orders_queryset)
	comparison_invoices = list(comparison_invoices_queryset)
	comparison_deliveries = list(comparison_deliveries_queryset)
	today = timezone.localdate()
	timezone_info = timezone.get_current_timezone()
	today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()), timezone_info)
	tomorrow_start = timezone.make_aware(datetime.combine(today + timedelta(days=1), datetime.min.time()), timezone_info)
	today_deliveries = list(
		Delivery.objects.select_related('driver', 'invoice__cliente').prefetch_related('payments', 'invoice__items').filter(
			estado__in=completed_statuses,
			delivered_at__gte=today_start,
			delivered_at__lt=tomorrow_start,
		).order_by('-delivered_at', '-updated_at')
	)
	return {
		'orders': orders,
		'invoices': invoices,
		'deliveries': deliveries,
		'invoice_items': invoice_items,
		'comparison_orders': comparison_orders,
		'comparison_invoices': comparison_invoices,
		'comparison_deliveries': comparison_deliveries,
		'today_deliveries': today_deliveries,
		'filter_options': {
			'drivers': sorted({(delivery.driver_id, delivery.driver.get_full_name() or delivery.driver.username) for delivery in deliveries if delivery.driver_id}, key=lambda item: item[1].lower()),
			'vendors': sorted({(invoice.pedido.vendedor_id, invoice.pedido.vendedor.get_full_name() or invoice.pedido.vendedor.username) for invoice in invoices if getattr(invoice.pedido, 'vendedor_id', None)}, key=lambda item: item[1].lower()),
			'customers': sorted({(invoice.cliente_id, invoice.cliente.nombre_empresa) for invoice in invoices if invoice.cliente_id}, key=lambda item: item[1].lower()),
		},
	}


def _build_dashboard_context(request, period, raw_data, filters=None):
	filters = filters or {'driver_id': None, 'vendor_id': None, 'customer_id': None, 'section': 'all'}
	focus = parse_focus(request)
	orders = raw_data['orders']
	invoices = raw_data['invoices']
	deliveries = raw_data['deliveries']
	invoice_items = raw_data['invoice_items']
	comparison_orders = raw_data['comparison_orders']
	comparison_invoices = raw_data['comparison_invoices']
	comparison_deliveries = raw_data['comparison_deliveries']
	today_deliveries = raw_data['today_deliveries']

	close_snapshot = _build_close_snapshot(deliveries)
	comparison_close = _build_close_snapshot(comparison_deliveries)
	today_close = _build_close_snapshot(today_deliveries)

	product_rows_full = enrich_product_rows_with_margin(invoice_items)
	product_rows = _add_bar_metadata(product_rows_full, 'revenue')
	product_rankings = build_product_rankings(product_rows_full)
	top_products = product_rows[:10]
	low_products = list(reversed(product_rows[-10:])) if product_rows else []

	customer_rows_full = enrich_customer_rows(invoices)
	customer_rows = _add_bar_metadata(customer_rows_full, 'sales_amount')
	customer_rankings = build_customer_rankings(customer_rows_full)

	driver_rows = _build_driver_rows(deliveries)
	vendor_rows = _add_bar_metadata(_build_vendor_rows(orders, invoices), 'sales_amount')
	creator_rows = _add_bar_metadata(_build_creator_rows(invoices), 'total_amount')
	category_rows = _add_bar_metadata(_build_category_rows(invoice_items), 'revenue')
	brand_rows = _add_bar_metadata(build_brand_rows(invoice_items), 'revenue')
	payment_method_rows = _build_payment_method_rows(deliveries)
	trend_rows = _build_trend_rows(period, orders, invoices, deliveries)
	trend_chart = _build_trend_chart(trend_rows)
	category_chart = _build_donut_chart(category_rows, value_key='revenue')
	payment_chart = _build_donut_chart(payment_method_rows, value_key='amount', label_key='label')
	recent_close_rows = _build_recent_close_rows(deliveries)

	inventory = build_inventory_snapshot()
	customer_mix = build_new_vs_returning_customers(invoices=invoices, period_start=period['start_datetime'])
	summary_cards = append_inventory_kpi_cards(
		build_bi_kpi_cards(
			orders=orders,
			invoices=invoices,
			deliveries=deliveries,
			close_snapshot=close_snapshot,
			comparison_orders=comparison_orders,
			comparison_invoices=comparison_invoices,
			comparison_close=comparison_close,
			invoice_items=invoice_items,
		),
		inventory,
		customer_mix,
	)
	period_sales = build_period_sales_strip(invoice_model=Invoice)
	never_sold = build_never_sold_products(sold_names=[row['name'] for row in product_rows_full])

	chart_payloads = {
		'trend': _build_trend_chart_payload(trend_rows),
		'categories': _build_donut_chart_payload(category_chart),
		'payments': _build_donut_chart_payload(payment_chart),
		'drivers': _build_driver_chart_payload(driver_rows),
		'recent_close': _build_recent_close_chart_payload(recent_close_rows),
		'vendors': _build_vendor_chart_payload(vendor_rows),
		'creators': _build_creator_chart_payload(creator_rows),
		'low_rotation': _build_low_rotation_chart_payload(low_products),
		'products': {
			'labels': [row['name'][:28] for row in top_products[:8]],
			'units': [row['units_sold'] for row in top_products[:8]],
			'revenue': [float(row['revenue']) for row in top_products[:8]],
		},
		'customers': {
			'labels': [row['name'][:28] for row in customer_rows[:8]],
			'sales': [float(row['sales_amount']) for row in customer_rows[:8]],
		},
		'brands': {
			'labels': [row['name'][:28] for row in brand_rows[:8]],
			'revenue': [float(row['revenue']) for row in brand_rows[:8]],
		},
	}

	return {
		'period': period,
		'period_choices': PERIOD_CHOICES,
		'active_filters': {
			'driver_id': filters.get('driver_id'),
			'vendor_id': filters.get('vendor_id'),
			'customer_id': filters.get('customer_id'),
			'section': filters.get('section', 'all'),
			'focus': focus,
		},
		'filter_options': raw_data.get('filter_options', {'drivers': [], 'vendors': [], 'customers': []}),
		'section_choices': [(key, label) for key, label in EXPORTABLE_SECTIONS.items()],
		'export_querystring': _build_export_querystring(request, period),
		'summary_cards': summary_cards,
		'period_sales': period_sales,
		'inventory': inventory,
		'customer_mix': customer_mix,
		'product_rankings': product_rankings,
		'customer_rankings': customer_rankings,
		'product_rows_full': product_rows_full[:50],
		'customer_rows_full': customer_rows_full[:50],
		'brand_rows': brand_rows[:10],
		'never_sold_products': never_sold,
		'smart_questions': build_smart_question_presets(),
		'close_snapshot': close_snapshot,
		'close_payment_rows': _build_payment_rows(close_snapshot['payment_totals']),
		'today_close': today_close,
		'driver_rows': driver_rows,
		'vendor_rows': vendor_rows[:12],
		'creator_rows': creator_rows[:8],
		'customer_rows': customer_rows[:12],
		'category_rows': category_rows[:12],
		'category_chart': category_chart,
		'payment_method_rows': payment_method_rows,
		'payment_chart': payment_chart,
		'top_products': top_products,
		'low_products': low_products,
		'recent_close_rows': recent_close_rows,
		'comparison_rows': _build_comparison_rows(orders, invoices, close_snapshot, comparison_orders, comparison_invoices, comparison_close),
		'focus_alerts': _build_focus_alerts(close_snapshot, top_products, low_products, driver_rows),
		'chart_payloads': chart_payloads,
		'trend_rows': trend_rows,
		'trend_chart': trend_chart,
		'generated_by': _resolve_report_author(request),
		'company_name': getattr(settings, 'COMPANY_NAME', None) or getattr(settings, 'APP_DISPLAY_NAME', None) or 'La Tortilla Grocery',
	}


def _resolve_report_author(request):
	user = getattr(request, 'user', None)
	if user is not None and getattr(user, 'is_authenticated', False):
		return user.get_full_name() or user.username
	return _('System')


@internal_permission_required('backoffice.reports.view')
def dashboard(request):
	period = _parse_range(request)
	filters = _parse_filters(request)
	context = _build_dashboard_context(request, period, _collect_report_data(period, filters=filters), filters=filters)
	return render(request, 'backoffice/reports_dashboard.html', context)


@internal_permission_required('backoffice.reports.view')
def export_excel(request):
	period = _parse_range(request)
	filters = _parse_filters(request)
	report_data = _build_dashboard_context(request, period, _collect_report_data(period, filters=filters), filters=filters)
	return _build_excel_response(period=period, report_data=report_data, section=filters['section'])


@internal_permission_required('backoffice.reports.view')
def export_pdf(request):
	period = _parse_range(request)
	filters = _parse_filters(request)
	report_data = _build_dashboard_context(request, period, _collect_report_data(period, filters=filters), filters=filters)
	return _build_pdf_response(period=period, report_data=report_data, section=filters['section'])


@internal_permission_required('backoffice.reports.view')
def export_csv(request):
	period = _parse_range(request)
	filters = _parse_filters(request)
	report_data = _build_dashboard_context(request, period, _collect_report_data(period, filters=filters), filters=filters)
	return _build_csv_response(period=period, report_data=report_data, section=filters['section'])


@internal_permission_required('backoffice.reports.view')
def send_email_now(request):
	if request.method != 'POST':
		return redirect('reportes_dashboard')
	period = _parse_range(request)
	filters = _parse_filters(request)
	report_data = _build_dashboard_context(request, period, _collect_report_data(period, filters=filters), filters=filters)
	recipients = send_reports_email(period=period, report_data=report_data, section=filters['section'])
	if recipients:
		messages.success(request, _('Report sent by email to %(count)s recipients.') % {'count': len(recipients)})
	else:
		messages.warning(request, _('No report recipients are configured yet.'))
	redirect_query = '&'.join(
		f'{key}={value}'
		for key, value in (
			('period', request.POST.get('period', 'today')),
			('start_date', request.POST.get('start_date', '')),
			('end_date', request.POST.get('end_date', '')),
			('driver_id', request.POST.get('driver_id', '')),
			('vendor_id', request.POST.get('vendor_id', '')),
			('customer_id', request.POST.get('customer_id', '')),
			('section', request.POST.get('section', 'all')),
		)
		if value
	)
	if redirect_query:
		return redirect(f"{reverse('reportes_dashboard')}?{redirect_query}")
	return redirect('reportes_dashboard')
