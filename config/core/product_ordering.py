from django.db.models.functions import Lower


def order_pedido_items_for_display(pedido):
	return (
		pedido.items
		.select_related('presentacion__producto')
		.annotate(_product_name_sort=Lower('presentacion__producto__nombre'))
		.order_by('_product_name_sort', 'id')
	)


def order_invoice_items_for_display(invoice):
	return (
		invoice.items
		.select_related('presentacion__producto', 'pedido_item')
		.annotate(_product_name_sort=Lower('producto_nombre'))
		.order_by('_product_name_sort', 'id')
	)
