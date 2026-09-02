"""Specialized Reports Center pages (Business Overview + detail reports)."""

from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.shortcuts import render
from django.utils.translation import gettext as _

from config.clientes.balance_summary import (
	build_customer_balance_summary,
	open_receivable_invoices_queryset,
)
from config.reportes.data_sources import (
	OVERVIEW_PERIOD_CHOICES,
	REPORT_NAV,
	build_expenses_snapshot,
	build_movements_snapshot,
	build_purchases_snapshot,
	build_receivables_snapshot,
	build_sales_snapshot,
	build_stagnant_products,
	build_top_products,
	build_valued_inventory,
	parse_overview_period,
	period_querystring,
)
from config.reportes.overview import build_business_overview
from config.usuarios.permissions import internal_permission_required

DECIMAL_ZERO = Decimal('0.00')


def _company_name():
	if getattr(settings, 'DEMO_MODE', False):
		return (
			getattr(settings, 'DEMO_BRAND_LEGAL_NAME', None)
			or getattr(settings, 'DEMO_BRAND_NAME', None)
			or getattr(settings, 'COMPANY_NAME', None)
			or getattr(settings, 'APP_DISPLAY_NAME', None)
			or 'Zyntra'
		)
	return (
		getattr(settings, 'COMPANY_NAME', None)
		or getattr(settings, 'APP_DISPLAY_NAME', None)
		or 'Reports'
	)


def _base_context(request, period, *, active_key):
	return {
		'period': period,
		'period_choices': OVERVIEW_PERIOD_CHOICES,
		'period_query': period_querystring(period),
		'report_nav': list(REPORT_NAV),
		'active_report': active_key,
		'company_name': _company_name(),
		'overview_url_name': 'reportes_dashboard',
		'bi_url_name': 'reportes_bi',
	}


def _detail_context(
	request,
	period,
	*,
	active_key,
	title,
	subtitle,
	summary_cards,
	columns,
	rows,
	empty_message=None,
):
	ctx = _base_context(request, period, active_key=active_key)
	ctx.update(
		{
			'title': title,
			'subtitle': subtitle,
			'summary_cards': summary_cards,
			'columns': columns,
			'rows': rows,
			'empty_message': empty_message or _('No rows for this period.'),
			'show_period_selector': True,
		}
	)
	return ctx


@internal_permission_required('backoffice.reports.view')
def business_overview(request):
	period = parse_overview_period(request)
	overview = build_business_overview(period)
	context = _base_context(request, period, active_key='overview')
	context.update(overview)
	context['page_title'] = _('Business Overview')
	return render(request, 'backoffice/reports_overview.html', context)


@internal_permission_required('backoffice.reports.view')
def inventory_report(request):
	period = parse_overview_period(request)
	inventory = build_valued_inventory()
	oos = [row for row in inventory['rows'] if row['available'] <= 0][:50]
	low = [
		row
		for row in inventory['rows']
		if 0 < row['available'] <= inventory['low_threshold']
	][:50]
	rows = [
		[
			row['name'],
			row['presentation'],
			row['brand'],
			row['available'],
			row['quick_inventory'],
		]
		for row in (oos + low)
	]
	context = _detail_context(
		request,
		period,
		active_key='inventory',
		title=_('Inventory health'),
		subtitle=_('Out of stock and low stock based on Available units.'),
		summary_cards=[
			{'label': _('Out of stock'), 'value': inventory['out_of_stock'], 'is_currency': False},
			{'label': _('Low stock'), 'value': inventory['low_stock'], 'is_currency': False},
			{'label': _('With stock'), 'value': inventory['with_stock'], 'is_currency': False},
			{'label': _('Inventory value'), 'value': inventory['inventory_value'], 'is_currency': True},
		],
		columns=[
			_('Product'),
			_('Presentation'),
			_('Brand'),
			_('Available'),
			_('Quick Inventory'),
		],
		rows=rows,
		empty_message=_('No out-of-stock or low-stock items right now.'),
	)
	context['show_period_selector'] = False
	return render(request, 'backoffice/reports_detail.html', context)


@internal_permission_required('backoffice.reports.view')
def stagnant_report(request):
	period = parse_overview_period(request)
	stagnant = build_stagnant_products(days=30, limit=100)
	total_value = sum((row.get('value') or DECIMAL_ZERO for row in stagnant), DECIMAL_ZERO)
	rows = [
		[
			row['name'],
			row['presentation'],
			row['brand'],
			row['available'],
			f"${row['value']:,.2f}",
			_('Never sold') if row.get('never_sold') else (row.get('days_since_sale') or '—'),
		]
		for row in stagnant
	]
	context = _detail_context(
		request,
		period,
		active_key='stagnant',
		title=_('Stagnant products'),
		subtitle=_('Presentations with Available stock and no invoiced sales in 30 days.'),
		summary_cards=[
			{'label': _('Stagnant SKUs'), 'value': len(stagnant), 'is_currency': False},
			{'label': _('Stagnant value'), 'value': total_value, 'is_currency': True},
		],
		columns=[
			_('Product'),
			_('Presentation'),
			_('Brand'),
			_('Available'),
			_('Value'),
			_('Days since sale'),
		],
		rows=rows,
	)
	context['show_period_selector'] = False
	return render(request, 'backoffice/reports_detail.html', context)


@internal_permission_required('backoffice.reports.view')
def sales_report(request):
	period = parse_overview_period(request)
	sales = build_sales_snapshot(period)
	top = build_top_products(period, limit=25)
	rows = [
		[
			row['name'],
			row['units_sold'],
			f"${row['revenue']:,.2f}",
			f"${row['profit']:,.2f}",
			row['invoices_count'],
		]
		for row in top
	]
	context = _detail_context(
		request,
		period,
		active_key='sales',
		title=_('Sales'),
		subtitle=_('Invoiced sales for %(period)s (QuickBooks imports excluded).')
		% {'period': period['label']},
		summary_cards=[
			{'label': _('Invoiced sales'), 'value': sales['invoiced_sales'], 'is_currency': True},
			{'label': _('Collected'), 'value': sales['collected'], 'is_currency': True},
			{'label': _('Orders'), 'value': sales['order_count'], 'is_currency': False},
			{'label': _('Invoices'), 'value': sales['invoice_count'], 'is_currency': False},
		],
		columns=[
			_('Product'),
			_('Units'),
			_('Revenue'),
			_('Profit'),
			_('Invoices'),
		],
		rows=rows,
	)
	return render(request, 'backoffice/reports_detail.html', context)


@internal_permission_required('backoffice.reports.view')
def receivables_report(request):
	period = parse_overview_period(request)
	receivables = build_receivables_snapshot()
	invoices = list(open_receivable_invoices_queryset().select_related('cliente'))
	by_cliente = defaultdict(list)
	for invoice in invoices:
		by_cliente[invoice.cliente_id].append(invoice)

	sorted_rows = []
	for cliente_invoices in by_cliente.values():
		cliente = cliente_invoices[0].cliente
		summary = build_customer_balance_summary(cliente, invoices=cliente_invoices)
		if not summary.has_balance:
			continue
		sorted_rows.append(
			(
				summary.total_open_balance,
				[
					cliente.nombre_empresa,
					f"${summary.total_open_balance:,.2f}",
					f"${summary.overdue_balance:,.2f}",
					summary.overdue_count,
					summary.max_aging_days,
				],
			)
		)
	sorted_rows.sort(key=lambda item: item[0], reverse=True)
	rows = [item[1] for item in sorted_rows]

	context = _detail_context(
		request,
		period,
		active_key='receivables',
		title=_('Receivables'),
		subtitle=_('Open customer balances from the accounts receivable ledger.'),
		summary_cards=[
			{
				'label': _('Total outstanding'),
				'value': receivables['total_outstanding'],
				'is_currency': True,
			},
			{
				'label': _('Overdue invoices'),
				'value': receivables['overdue_count'],
				'is_currency': False,
			},
			{
				'label': _('Customers with balance'),
				'value': receivables['customers_with_balance'],
				'is_currency': False,
			},
			{
				'label': _('Due this week'),
				'value': receivables['due_this_week'],
				'is_currency': False,
			},
		],
		columns=[
			_('Customer'),
			_('Open balance'),
			_('Overdue'),
			_('Overdue invoices'),
			_('Max aging (days)'),
		],
		rows=rows,
		empty_message=_('No open receivables right now.'),
	)
	context['show_period_selector'] = False
	return render(request, 'backoffice/reports_detail.html', context)


@internal_permission_required('backoffice.reports.view')
def finance_report(request):
	period = parse_overview_period(request)
	sales = build_sales_snapshot(period)
	receivables = build_receivables_snapshot()
	expenses = build_expenses_snapshot()
	rows = [
		[_('Invoiced sales'), f"${sales['invoiced_sales']:,.2f}", period['label']],
		[_('Collected'), f"${sales['collected']:,.2f}", period['label']],
		[_('Open receivables'), f"${receivables['total_outstanding']:,.2f}", _('Current')],
		[_('Overdue invoices'), receivables['overdue_count'], _('Current')],
		[_('Expenses'), expenses['label'], _('Module not available')],
	]
	context = _detail_context(
		request,
		period,
		active_key='finance',
		title=_('Finance snapshot'),
		subtitle=_('Money movement and receivables at a glance.'),
		summary_cards=[
			{'label': _('Sold'), 'value': sales['invoiced_sales'], 'is_currency': True},
			{'label': _('Collected'), 'value': sales['collected'], 'is_currency': True},
			{
				'label': _('Receivable'),
				'value': receivables['total_outstanding'],
				'is_currency': True,
			},
			{'label': _('Expenses'), 'value': expenses['label'], 'is_currency': False},
		],
		columns=[_('Metric'), _('Amount / value'), _('Scope')],
		rows=rows,
	)
	return render(request, 'backoffice/reports_detail.html', context)


@internal_permission_required('backoffice.reports.view')
def purchases_report(request):
	period = parse_overview_period(request)
	purchases = build_purchases_snapshot(period)
	rows = [
		[
			row['po_number'],
			row['supplier'],
			row['date'].isoformat() if row['date'] else '—',
			row['status'],
			f"${row['total']:,.2f}",
		]
		for row in purchases['rows']
	]
	context = _detail_context(
		request,
		period,
		active_key='purchases',
		title=_('Purchases'),
		subtitle=_('Supplier purchases recorded in %(period)s.') % {'period': period['label']},
		summary_cards=[
			{'label': _('Purchase orders'), 'value': purchases['count'], 'is_currency': False},
			{'label': _('Total'), 'value': purchases['total'], 'is_currency': True},
			{'label': _('Received'), 'value': purchases['received_count'], 'is_currency': False},
			{
				'label': _('Received total'),
				'value': purchases['received_total'],
				'is_currency': True,
			},
		],
		columns=[_('PO'), _('Supplier'), _('Date'), _('Status'), _('Total')],
		rows=rows,
	)
	return render(request, 'backoffice/reports_detail.html', context)


@internal_permission_required('backoffice.reports.view')
def valued_report(request):
	period = parse_overview_period(request)
	inventory = build_valued_inventory()
	# Show SKUs with stock first, highest value first.
	valued_rows = sorted(
		[row for row in inventory['rows'] if row['available'] > 0],
		key=lambda row: row['value'],
		reverse=True,
	)[:100]
	rows = [
		[
			row['name'],
			row['presentation'],
			row['available'],
			f"${row['unit_cost']:,.2f}",
			f"${row['value']:,.2f}",
		]
		for row in valued_rows
	]
	context = _detail_context(
		request,
		period,
		active_key='valued',
		title=_('Valued inventory'),
		subtitle=_('Available units × effective cost (RCost + Landed).'),
		summary_cards=[
			{'label': _('Inventory value'), 'value': inventory['inventory_value'], 'is_currency': True},
			{'label': _('SKUs'), 'value': inventory['sku_count'], 'is_currency': False},
			{'label': _('With stock'), 'value': inventory['with_stock'], 'is_currency': False},
			{'label': _('Out of stock'), 'value': inventory['out_of_stock'], 'is_currency': False},
		],
		columns=[
			_('Product'),
			_('Presentation'),
			_('Available'),
			_('Unit cost'),
			_('Line value'),
		],
		rows=rows,
	)
	context['show_period_selector'] = False
	return render(request, 'backoffice/reports_detail.html', context)


@internal_permission_required('backoffice.reports.view')
def movements_report(request):
	period = parse_overview_period(request)
	movements = build_movements_snapshot(period, limit=100)
	rows = [
		[
			row['when'].strftime('%Y-%m-%d %H:%M') if row['when'] else '—',
			row['product'],
			row['presentation'],
			row['type'],
			row['quantity'],
			row['delta_physical'],
			row['reference'],
		]
		for row in movements['rows']
	]
	context = _detail_context(
		request,
		period,
		active_key='movements',
		title=_('Inventory movements'),
		subtitle=_('Ledger activity in %(period)s.') % {'period': period['label']},
		summary_cards=[
			{'label': _('Movements'), 'value': movements['count'], 'is_currency': False},
		],
		columns=[
			_('When'),
			_('Product'),
			_('Presentation'),
			_('Type'),
			_('Qty'),
			_('Δ physical'),
			_('Reference'),
		],
		rows=rows,
	)
	return render(request, 'backoffice/reports_detail.html', context)
