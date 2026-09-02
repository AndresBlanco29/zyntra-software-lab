"""Business Overview composer for Reports Center."""

from decimal import Decimal

from django.utils.translation import gettext as _

from config.reportes.data_sources import (
	REPORT_NAV,
	build_expenses_snapshot,
	build_movements_snapshot,
	build_purchases_snapshot,
	build_receivables_snapshot,
	build_sales_snapshot,
	build_stagnant_products,
	build_top_products,
	build_valued_inventory,
	period_querystring,
	stagnant_inventory_value,
)


DECIMAL_ZERO = Decimal('0.00')


def _kpi(*, key, label, value, caption, detail_url_name, detail_query='', is_currency=False):
	return {
		'key': key,
		'label': label,
		'value': value,
		'caption': caption,
		'detail_url_name': detail_url_name,
		'detail_query': detail_query,
		'is_currency': is_currency,
	}


def _alert(*, severity, title, description, detail_url_name, detail_query=''):
	return {
		'severity': severity,
		'title': title,
		'description': description,
		'detail_url_name': detail_url_name,
		'detail_query': detail_query,
	}


def build_business_overview(period):
	"""Compose data_sources into the Business Overview payload."""
	pq = period_querystring(period)
	sales = build_sales_snapshot(period)
	receivables = build_receivables_snapshot()
	inventory = build_valued_inventory()
	stagnant = build_stagnant_products(days=30, limit=25)
	stagnant_value = stagnant_inventory_value(days=30, limit=500)
	top_products = build_top_products(period, limit=10)
	purchases = build_purchases_snapshot(period)
	movements = build_movements_snapshot(period, limit=25)
	expenses = build_expenses_snapshot()

	kpis = [
		_kpi(
			key='sales',
			label=_('Sales'),
			value=sales['invoiced_sales'],
			caption=_('Invoiced in %(period)s') % {'period': period['label']},
			detail_url_name='reportes_sales',
			detail_query=pq,
			is_currency=True,
		),
		_kpi(
			key='collected',
			label=_('Collected'),
			value=sales['collected'],
			caption=_('Payments recorded in period'),
			detail_url_name='reportes_sales',
			detail_query=pq,
			is_currency=True,
		),
		_kpi(
			key='receivables',
			label=_('Receivables'),
			value=receivables['total_outstanding'],
			caption=_('%(count)s customers with balance')
			% {'count': receivables['customers_with_balance']},
			detail_url_name='reportes_receivables',
			is_currency=True,
		),
		_kpi(
			key='inventory_value',
			label=_('Inventory value'),
			value=inventory['inventory_value'],
			caption=_('Available × effective cost'),
			detail_url_name='reportes_valued',
			is_currency=True,
		),
		_kpi(
			key='out_of_stock',
			label=_('Out of stock'),
			value=inventory['out_of_stock'],
			caption=_('%(count)s SKUs with low stock') % {'count': inventory['low_stock']},
			detail_url_name='reportes_inventory',
			is_currency=False,
		),
		_kpi(
			key='stagnant_30',
			label=_('Stagnant (30d)'),
			value=len(stagnant),
			caption=_('With stock and no recent sales'),
			detail_url_name='reportes_stagnant',
			is_currency=False,
		),
	]

	alerts = []
	if inventory['out_of_stock']:
		alerts.append(
			_alert(
				severity='danger',
				title=_('Out of stock'),
				description=_('%(count)s active SKUs have zero available units.')
				% {'count': inventory['out_of_stock']},
				detail_url_name='reportes_inventory',
			)
		)
	if inventory['low_stock']:
		alerts.append(
			_alert(
				severity='warning',
				title=_('Low stock'),
				description=_('%(count)s SKUs are at or below the low-stock threshold.')
				% {'count': inventory['low_stock']},
				detail_url_name='reportes_inventory',
			)
		)
	if stagnant:
		alerts.append(
			_alert(
				severity='warning',
				title=_('Stagnant products'),
				description=_('%(count)s presentations have stock but no sales in 30 days.')
				% {'count': len(stagnant)},
				detail_url_name='reportes_stagnant',
			)
		)
	if receivables['total_outstanding'] > DECIMAL_ZERO:
		alerts.append(
			_alert(
				severity='info',
				title=_('Open receivables'),
				description=_('%(amount)s outstanding · %(overdue)s overdue invoices')
				% {
					'amount': f"${receivables['total_outstanding']:,.2f}",
					'overdue': receivables['overdue_count'],
				},
				detail_url_name='reportes_receivables',
			)
		)
	high_value_stagnant = [row for row in stagnant if row.get('value', DECIMAL_ZERO) >= Decimal('500.00')]
	if high_value_stagnant:
		alerts.append(
			_alert(
				severity='danger',
				title=_('High-value stagnant stock'),
				description=_('%(count)s stagnant lines hold $500+ in inventory value.')
				% {'count': len(high_value_stagnant)},
				detail_url_name='reportes_stagnant',
			)
		)

	slow_products = stagnant[:10]

	return {
		'period': period,
		'period_query': pq,
		'kpis': kpis,
		'alerts': alerts,
		'top_products': top_products,
		'slow_products': slow_products,
		'money': {
			'sold': sales['invoiced_sales'],
			'collected': sales['collected'],
			'receivable': receivables['total_outstanding'],
			'orders': sales['order_count'],
			'invoices': sales['invoice_count'],
		},
		'inventory': {
			'inventory_value': inventory['inventory_value'],
			'out_of_stock': inventory['out_of_stock'],
			'low_stock': inventory['low_stock'],
			'with_stock': inventory['with_stock'],
			'sku_count': inventory['sku_count'],
			'stagnant_30': len(stagnant),
			'stagnant_value': stagnant_value,
		},
		'sales': sales,
		'receivables': receivables,
		'purchases': purchases,
		'movements': movements,
		'expenses': expenses,
		'report_nav': list(REPORT_NAV),
	}
