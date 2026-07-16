from collections import defaultdict

from django.db.models import Count, Prefetch, Sum

from config.facturacion.models import InvoiceItem
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Presentacion, Producto


FAVORITE_PRODUCTS_LIMIT = 8


def _rank_purchase_stats(stats, *, limit):
	ranked = sorted(
		stats.items(),
		key=lambda pair: (
			-pair[1]['count'],
			pair[1]['last_at'] is None,
			-(pair[1]['last_at'].timestamp() if pair[1]['last_at'] else 0),
			-pair[0],
		),
	)
	return [
		{
			'product_id': product_id,
			'preferred_presentation_id': data['last_presentation_id'],
			'times_bought': data['count'],
		}
		for product_id, data in ranked[:limit]
	]


def _accumulate_line(*, stats, product_id, presentation_id, quantity, purchased_at):
	entry = stats[product_id]
	entry['count'] += int(quantity or 0) or 1
	if entry['last_presentation_id'] is None:
		entry['last_presentation_id'] = presentation_id
		entry['last_at'] = purchased_at
		return
	if purchased_at and (entry['last_at'] is None or purchased_at > entry['last_at']):
		entry['last_presentation_id'] = presentation_id
		entry['last_at'] = purchased_at


def list_cliente_favorite_product_ids(*, cliente, limit=FAVORITE_PRODUCTS_LIMIT):
	"""Return product ids ranked by purchase frequency (invoices first, else orders)."""
	if cliente is None:
		return []

	stats = defaultdict(lambda: {'count': 0, 'last_presentation_id': None, 'last_at': None})
	invoice_items = (
		InvoiceItem.objects.filter(
			invoice__cliente=cliente,
			invoice__estado='GENERADA',
			presentacion_id__isnull=False,
			presentacion__producto__activo=True,
		)
		.select_related('presentacion', 'invoice')
		.order_by('-invoice__creada_en', '-id')
	)
	for item in invoice_items.iterator(chunk_size=200):
		_accumulate_line(
			stats=stats,
			product_id=item.presentacion.producto_id,
			presentation_id=item.presentacion_id,
			quantity=item.cantidad_facturada,
			purchased_at=item.invoice.creada_en,
		)

	if not stats:
		pedido_items = (
			PedidoItem.objects.filter(
				pedido__cliente=cliente,
				pedido__estado__in=[
					'RECIBIDO',
					'EN_GESTION',
					'LISTO_PARA_PICKING',
					'PARA_VERIFICAR',
					'VERIFICADO_AJUSTADO',
					'INVOICE_GENERADA',
					'DESPACHADO',
				],
				presentacion_id__isnull=False,
				presentacion__producto__activo=True,
			)
			.select_related('presentacion', 'pedido')
			.order_by('-pedido__creada_en', '-id')
		)
		for item in pedido_items.iterator(chunk_size=200):
			_accumulate_line(
				stats=stats,
				product_id=item.presentacion.producto_id,
				presentation_id=item.presentacion_id,
				quantity=item.cantidad,
				purchased_at=item.pedido.creada_en,
			)

	return _rank_purchase_stats(stats, limit=limit)


def load_cliente_favorite_productos(*, cliente, hydrate_fn, attach_promos_fn, limit=FAVORITE_PRODUCTS_LIMIT):
	"""Hydrated Producto list for the catalog favorites strip."""
	ranked = list_cliente_favorite_product_ids(cliente=cliente, limit=limit)
	if not ranked:
		return []

	product_ids = [row['product_id'] for row in ranked]
	preferred_by_product = {row['product_id']: row['preferred_presentation_id'] for row in ranked}
	presentaciones_qs = Presentacion.objects.select_related('producto').order_by('nombre', 'id')
	productos = list(
		Producto.objects.filter(id__in=product_ids, activo=True)
		.select_related('categoria', 'marca')
		.prefetch_related(Prefetch('presentaciones', queryset=presentaciones_qs, to_attr='presentaciones_prefetch'))
	)
	productos = hydrate_fn(productos)
	productos = attach_promos_fn(productos)
	by_id = {producto.id: producto for producto in productos}
	ordered = []
	for row in ranked:
		producto = by_id.get(row['product_id'])
		if producto is None:
			continue
		preferred_id = preferred_by_product.get(producto.id)
		producto.preferred_presentation_id = preferred_id
		if preferred_id:
			presentations = list(getattr(producto, 'presentaciones_prefetch', []) or [])
			preferred = next((p for p in presentations if p.id == preferred_id), None)
			if preferred is not None:
				producto.primera_presentacion = preferred
		ordered.append(producto)
	return ordered


def list_cliente_purchase_orders(*, cliente):
	"""Customer purchase orders newest-first with line counts."""
	if cliente is None:
		return Pedido.objects.none()
	return (
		Pedido.objects.filter(cliente=cliente)
		.annotate(
			product_line_count=Count('items', distinct=True),
			product_unit_count=Sum('items__cantidad'),
		)
		.order_by('-creada_en', '-id')
	)


def merge_pedido_into_session_cart(*, carrito, pedido, price_fn, promo_fn):
	"""Add every active line from a past order into the session cart. Returns (carrito, added_count)."""
	carrito = dict(carrito or {})
	added_count = 0
	items = (
		pedido.items.select_related('presentacion__producto')
		.filter(presentacion_id__isnull=False, presentacion__producto__activo=True)
		.order_by('id')
	)
	for item in items:
		presentacion = item.presentacion
		if presentacion is None:
			continue
		producto = presentacion.producto
		cantidad = int(item.cantidad or 0)
		if cantidad <= 0:
			continue
		precio = price_fn(presentacion=presentacion)
		key = str(presentacion.id)
		if key in carrito:
			carrito[key]['cantidad'] = int(carrito[key].get('cantidad') or 0) + cantidad
			carrito[key]['precio'] = float(precio)
		else:
			carrito[key] = {
				'producto_id': producto.id,
				'presentacion_id': presentacion.id,
				'nombre': producto.nombre,
				'cantidad': cantidad,
				'precio': float(precio),
			}
		promo_fn(carrito[key], precio_unitario=precio, presentacion=presentacion)
		added_count += 1
	return carrito, added_count
