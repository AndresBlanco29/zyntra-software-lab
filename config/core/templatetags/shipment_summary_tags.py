from django import template

from config.core.shipment_summary import (
	build_shipment_summary_from_cart_entries,
	build_shipment_summary_from_invoice,
	build_shipment_summary_from_pedido_items,
	build_shipment_summary_from_vendor_cart_products,
	unwrap_quote_rows,
)

register = template.Library()


def _empty_summary():
	return {'total_cases': 0, 'total_weight': 0}


@register.inclusion_tag('includes/product_shipment_summary.html')
def product_shipment_summary(source, value, quantity='cantidad'):
	if not value:
		return {'shipment_summary': _empty_summary()}

	if source == 'invoice':
		summary = build_shipment_summary_from_invoice(value)
	elif source == 'pedido_items':
		summary = build_shipment_summary_from_pedido_items(value, quantity_attr=quantity)
	elif source == 'pedido':
		summary = build_shipment_summary_from_pedido_items(value.items.all(), quantity_attr=quantity)
	elif source in {'cotizacion_items', 'quote_rows'}:
		summary = build_shipment_summary_from_pedido_items(unwrap_quote_rows(value), quantity_attr='cantidad')
	elif source == 'cart':
		summary = build_shipment_summary_from_cart_entries(value)
	elif source == 'vendor_products':
		summary = build_shipment_summary_from_vendor_cart_products(value)
	else:
		summary = _empty_summary()

	return {'shipment_summary': summary}
