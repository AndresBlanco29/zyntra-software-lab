from django import template

from config.core.shipment_summary import (
	build_shipment_summary_from_cart_entries,
	build_shipment_summary_from_pedido_items,
	build_shipment_summary_from_vendor_cart_products,
	unwrap_quote_rows,
	with_total_pallets,
)
from config.facturacion.services import build_invoice_shipment_summary
register = template.Library()


def _empty_summary():
	return {'total_cases': 0, 'total_weight': 0, 'total_pallets': None}


def _resolve_pallets(source, value, pallets):
	if pallets is not None:
		return pallets
	if source == 'invoice':
		pedido = getattr(value, 'pedido', None)
		return getattr(pedido, 'cantidad_pallets', None) if pedido is not None else None
	if source == 'pedido':
		return getattr(value, 'cantidad_pallets', None)
	return None


@register.inclusion_tag('includes/product_shipment_summary.html')
def product_shipment_summary(source, value, quantity='cantidad', editable_pallets=False, pallets=None):
	if not value:
		return {
			'shipment_summary': _empty_summary(),
			'editable_pallets': bool(editable_pallets),
		}

	if source == 'invoice':
		summary = build_invoice_shipment_summary(value)
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

	summary = with_total_pallets(summary, _resolve_pallets(source, value, pallets))
	return {
		'shipment_summary': summary,
		'editable_pallets': bool(editable_pallets),
	}
