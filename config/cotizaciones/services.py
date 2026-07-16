from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from config.clientes.assignment import filter_clientes_for_vendedor
from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.pedidos.services import (
	calcular_subtotal_item_pedido,
	normalizar_descuento_item_pedido,
)
from config.usuarios.permissions import user_has_permission


def _quantize_money(value):
	return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def user_can_view_cotizacion(user, cotizacion):
	if user_has_permission(user, 'backoffice.quotes.view'):
		return True
	if user_has_permission(user, 'vendor.quotes.view'):
		return filter_clientes_for_vendedor(
			Cliente.objects.filter(pk=cotizacion.cliente_id),
			user,
		).exists()
	return False


def user_can_manage_cotizacion(user, cotizacion):
	if user_has_permission(user, 'backoffice.quotes.manage'):
		return True
	if user_has_permission(user, 'vendor.quotes.manage'):
		return filter_clientes_for_vendedor(
			Cliente.objects.filter(pk=cotizacion.cliente_id),
			user,
		).exists()
	return False


@transaction.atomic
def crear_cotizacion_desde_items(
	*,
	cliente,
	items_payload,
	vendedor=None,
	nota_cliente='',
	creado_por=None,
):
	"""Create a staff/sales draft quote with prices and discounts already confirmed."""
	if not items_payload:
		raise ValidationError(_('You must add at least one item to create the quotation.'))

	nota_texto = (nota_cliente or '').strip()
	vendedor_usuario = None
	if vendedor is not None and getattr(vendedor, 'role', '') == 'vendedor':
		vendedor_usuario = vendedor
	elif creado_por is not None and getattr(creado_por, 'role', '') == 'vendedor':
		vendedor_usuario = creado_por

	cotizacion = Cotizacion.objects.create(
		cliente=cliente,
		vendedor=vendedor_usuario,
		estado='BORRADOR',
		nota_cliente=nota_texto,
		backoffice_pricing_confirmed=True,
		total=Decimal('0.00'),
	)

	line_items = []
	total = Decimal('0.00')
	for payload in items_payload:
		presentacion = payload['presentacion']
		cantidad = max(int(payload.get('cantidad') or 1), 1)
		precio = _quantize_money(payload.get('precio', 0))
		descuento_aplicado, descuento_monto = normalizar_descuento_item_pedido(
			precio=precio,
			descuento_aplicado=payload.get('descuento_aplicado', False),
			descuento_monto=payload.get('descuento_monto', 0),
		)
		subtotal = calcular_subtotal_item_pedido(
			precio=precio,
			cantidad=cantidad,
			descuento_aplicado=descuento_aplicado,
			descuento_monto=descuento_monto,
		)
		line_items.append(
			CotizacionItem(
				cotizacion=cotizacion,
				presentacion=presentacion,
				cantidad=cantidad,
				precio=precio,
				descuento_aplicado=descuento_aplicado,
				descuento_monto=descuento_monto,
				subtotal=subtotal,
			)
		)
		total += subtotal

	CotizacionItem.objects.bulk_create(line_items)
	cotizacion.total = total
	cotizacion.save(update_fields=['total'])
	from config.productos.promotions import asegurar_promociones_en_cotizacion
	asegurar_promociones_en_cotizacion(cotizacion)
	cotizacion.refresh_from_db(fields=['total'])
	return cotizacion
