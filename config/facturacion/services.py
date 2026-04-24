import base64
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from config.inventario.services import _apply_inventory_change, _lock_stock_records
from config.notificaciones.models import crear_notificacion_backoffice

from .models import Delivery, DeliveryEvidencePhoto, DeliveryNotificationLog, Invoice, InvoiceItem, NotaAjuste, NotaAjusteItem


DEFAULT_SUGGESTED_PROFIT_PERCENTAGE = Decimal('30.00')


def _to_decimal(value, default='0'):
	try:
		return Decimal(str(value if value is not None else default))
	except (InvalidOperation, TypeError, ValueError):
		return Decimal(str(default))


def _quantize_money(value):
	return _to_decimal(value, '0').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _calculate_suggested_unit_price_from_profit(base_unit_price, profit_percentage=DEFAULT_SUGGESTED_PROFIT_PERCENTAGE):
	base_unit_decimal = _quantize_money(base_unit_price)
	profit_decimal = _to_decimal(profit_percentage, DEFAULT_SUGGESTED_PROFIT_PERCENTAGE)
	divisor = Decimal('1') - (profit_decimal / Decimal('100'))
	if divisor <= 0:
		return base_unit_decimal
	return (base_unit_decimal / divisor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _validate_invoice_generation(pedido, metodo_entrega, driver):
	if pedido.estado != 'VERIFICADO_AJUSTADO':
		raise ValidationError(_('The invoice can only be generated from a verified and adjusted picking order.'))
	if pedido.picking_bloqueado:
		raise ValidationError(_('The order is blocked by an unresolved selector note.'))
	if hasattr(pedido, 'invoice'):
		raise ValidationError(_('This purchase order already has an invoice.'))
	if metodo_entrega == 'RUTA_DRIVER' and driver is None:
		raise ValidationError(_('A driver is required for route deliveries.'))
	if driver is not None and getattr(driver, 'role', '') != 'driver':
		raise ValidationError(_('Only users with driver role can be assigned.'))


def resolve_presentacion_suggested_unit_price(*, presentacion, base_case_price):
	case_price = _quantize_money(base_case_price)
	units = max(int(getattr(presentacion, 'unidades', 0) or 0), 1) if presentacion else 1
	base_unit_price = (case_price / Decimal(str(units))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
	return _calculate_suggested_unit_price_from_profit(base_unit_price)


@transaction.atomic
def ensure_delivery_for_invoice(invoice):
	if invoice.metodo_entrega != 'RUTA_DRIVER':
		return None
	if hasattr(invoice, 'delivery'):
		return invoice.delivery
	cliente = invoice.cliente
	return Delivery.objects.create(
		invoice=invoice,
		driver=invoice.driver,
		delivery_address=cliente.direccion,
		delivery_city=cliente.ciudad,
		delivery_state=cliente.estado,
		delivery_postal_code=cliente.codigo_postal or '',
		delivery_country=cliente.pais or 'USA',
	)


def build_google_maps_route_url(deliveries):
	delivery_list = list(deliveries)
	if not delivery_list:
		raise ValidationError(_('Select at least one assigned invoice to generate the route.'))
	addresses = []
	for delivery in delivery_list:
		address = (delivery.route_query_address or '').strip()
		if not address:
			raise ValidationError(_('At least one delivery address is required to generate the route.'))
		addresses.append(address.replace(' ', '+'))
	url = f'https://www.google.com/maps/dir/?api=1&destination={addresses[-1]}&travelmode=driving'
	if len(addresses) > 1:
		url += '&waypoints=' + '|'.join(addresses[:-1])
	return url


def _create_invoice_notification(invoice):
	crear_notificacion_backoffice(
		titulo=_('Invoice %(number)s generated') % {'number': invoice.numero},
		mensaje=_('Invoice %(number)s is ready for dispatch review.') % {'number': invoice.numero},
		tipo='PEDIDO',
		url=f'/facturacion/backoffice/invoices/{invoice.id}/',
	)


@transaction.atomic
def generar_invoice_desde_picking(
	*,
	pedido,
	metodo_entrega,
	driver,
	usuario,
	suggested_unit_prices=None,
	estimated_delivery_at=None,
):
	_validate_invoice_generation(pedido, metodo_entrega, driver)
	suggested_unit_prices = suggested_unit_prices or {}

	invoice = Invoice.objects.create(
		pedido=pedido,
		cliente=pedido.cliente,
		metodo_entrega=metodo_entrega,
		driver=driver,
		despachador_notificado=True,
		notificado_en=timezone.now(),
		creada_por=usuario,
	)

	total = Decimal('0.00')
	invoice_items = []
	for item in pedido.items.select_related('presentacion__producto').all():
		quantity = int(item.cantidad or 0)
		if quantity <= 0:
			continue
		line_total = _quantize_money(_to_decimal(item.precio) * Decimal(str(quantity)))
		suggested_unit_price = suggested_unit_prices.get(item.id)
		if suggested_unit_price in (None, ''):
			suggested_unit_price = resolve_presentacion_suggested_unit_price(
				presentacion=item.presentacion,
				base_case_price=item.precio,
			)
		else:
			suggested_unit_price = _quantize_money(suggested_unit_price)
		invoice_items.append(InvoiceItem(
			invoice=invoice,
			pedido_item=item,
			presentacion=item.presentacion,
			producto_nombre=item.presentacion.producto.nombre,
			presentacion_nombre=item.presentacion.nombre,
			cantidad_facturada=quantity,
			precio_unitario=_quantize_money(item.precio),
			precio_venta_sugerido_unitario=suggested_unit_price,
			subtotal=line_total,
		))
		total += line_total

	if not invoice_items:
		raise ValidationError(_('Add at least one verified item before generating the invoice.'))

	InvoiceItem.objects.bulk_create(invoice_items)
	invoice.subtotal = _quantize_money(total)
	invoice.total_neto = _quantize_money(total)
	invoice.saldo_cliente = _quantize_money(total)
	invoice.save(update_fields=['subtotal', 'total_neto', 'saldo_cliente', 'actualizada_en'])

	pedido.estado = 'INVOICE_GENERADA'
	pedido.save(update_fields=['estado', 'actualizada_en'])

	if metodo_entrega == 'RUTA_DRIVER':
		delivery = ensure_delivery_for_invoice(invoice)
		if delivery is not None and estimated_delivery_at is not None:
			delivery.estimated_delivery_at = estimated_delivery_at
			delivery.save(update_fields=['estimated_delivery_at', 'updated_at'])

	_create_invoice_notification(invoice)
	return invoice


def _build_signature_content_file(signature_data, delivery_id):
	if not signature_data:
		raise ValidationError(_('Customer signature is required to complete the delivery.'))
	if ',' in signature_data:
		_, encoded = signature_data.split(',', 1)
	else:
		encoded = signature_data
	try:
		binary = base64.b64decode(encoded)
	except (ValueError, TypeError) as exc:
		raise ValidationError(_('Customer signature could not be processed.')) from exc
	return ContentFile(binary, name=f'delivery-signature-{delivery_id}.png')


def _create_delivery_notification_logs(delivery, status):
	cliente_usuario = getattr(delivery.invoice.cliente, 'usuario', None)
	for channel, target in (
		('EMAIL', getattr(cliente_usuario, 'email', '') or ''),
		('SMS', delivery.invoice.cliente.telefono or ''),
		('WHATSAPP', delivery.invoice.cliente.telefono or ''),
	):
		DeliveryNotificationLog.objects.create(
			delivery=delivery,
			channel=channel,
			status=status,
			target=target,
			message=delivery.invoice.numero,
		)


@transaction.atomic
def start_delivery_route(*, delivery, driver_user):
	if delivery.driver_id != driver_user.id:
		raise PermissionDenied(_('You are not assigned to this delivery.'))
	if delivery.is_completed:
		raise ValidationError(_('Completed deliveries cannot start a new route.'))
	if delivery.estado != 'EN_RUTA':
		delivery.estado = 'EN_RUTA'
		delivery.route_started_at = timezone.now()
		delivery.save(update_fields=['estado', 'route_started_at', 'updated_at'])
	return delivery


@transaction.atomic
def complete_driver_delivery(*, delivery, driver_user, payload, evidence_files):
	if delivery.driver_id != driver_user.id:
		raise PermissionDenied(_('You are not assigned to this delivery.'))
	if delivery.is_completed:
		raise ValidationError(_('This delivery was already completed.'))

	estado_pago = (payload.get('estado_pago') or '').strip()
	recibido_por = (payload.get('recibido_por') or '').strip()
	metodo_pago = (payload.get('metodo_pago') or '').strip()
	motivo_no_pago = (payload.get('motivo_no_pago') or '').strip()
	monto_pagado = _quantize_money(payload.get('monto_pagado') or '0')
	signature_file = _build_signature_content_file(payload.get('firma_cliente_data'), delivery.id)

	if estado_pago not in {'PAGADO', 'NO_PAGADO'}:
		raise ValidationError(_('Select a valid payment status.'))
	if not recibido_por:
		raise ValidationError(_('Recipient name is required for delivered orders.'))
	if estado_pago == 'PAGADO':
		if not metodo_pago:
			raise ValidationError(_('A payment method is required when the delivery is paid.'))
		if monto_pagado <= 0:
			raise ValidationError(_('Paid deliveries must include a payment amount greater than zero.'))
		if monto_pagado > delivery.invoice.saldo_cliente:
			raise ValidationError(_('The paid amount cannot exceed the customer balance.'))
	else:
		if not motivo_no_pago:
			raise ValidationError(_('A reason is required when the customer does not pay.'))
		if not evidence_files:
			raise ValidationError(_('Upload at least one evidence photo when the customer does not pay.'))

	if delivery.estado != 'EN_RUTA':
		start_delivery_route(delivery=delivery, driver_user=driver_user)

	delivery.estado_pago = estado_pago
	delivery.metodo_pago = metodo_pago if estado_pago == 'PAGADO' else ''
	delivery.monto_pagado = monto_pagado if estado_pago == 'PAGADO' else Decimal('0.00')
	delivery.recibido_por = recibido_por
	delivery.motivo_no_pago = motivo_no_pago if estado_pago == 'NO_PAGADO' else ''
	delivery.notas_driver = (payload.get('notas_driver') or '').strip()
	delivery.firma_cliente = signature_file
	delivery.firma_recibida_en = timezone.now()
	delivery.delivered_at = timezone.now()
	delivery.notifications_sent_at = timezone.now()
	if estado_pago == 'PAGADO':
		delivery.estado = 'ENTREGADA_PAGADA'
		delivery.client_blocked_on_delivery = False
		delivery.invoice.cliente.credit_hold = False
		delivery.invoice.cliente.save(update_fields=['credit_hold'])
		_create_delivery_notification_logs(delivery, 'SENT')
	else:
		delivery.estado = 'ENTREGADA_SIN_PAGO'
		delivery.client_blocked_on_delivery = True
		delivery.invoice.cliente.credit_hold = True
		delivery.invoice.cliente.save(update_fields=['credit_hold'])
		_create_deliveryNotificationLogs = _create_delivery_notification_logs
		_create_deliveryNotificationLogs(delivery, 'FAILED')

	delivery.save()
	for uploaded_file in evidence_files:
		DeliveryEvidencePhoto.objects.create(delivery=delivery, image=uploaded_file)

	pedido = delivery.invoice.pedido
	pedido.estado = 'DESPACHADO'
	pedido.save(update_fields=['estado', 'actualizada_en'])
	return delivery


def _extract_note_presentacion(invoice_item):
	if invoice_item.presentacion_id:
		return invoice_item.presentacion
	if invoice_item.pedido_item_id:
		return invoice_item.pedido_item.presentacion
	return None


def _apply_credit_note_inventory(*, nota, movement_type, delta_fisico, created_by=None):
	presentacion_ids = [item.presentacion_id for item in nota.items.all() if item.presentacion_id]
	if not presentacion_ids:
		return
	stock_map = _lock_stock_records(presentacion_ids)
	for note_item in nota.items.select_related('presentacion__producto').all():
		if not note_item.presentacion_id:
			continue
		stock = stock_map[note_item.presentacion_id]
		_apply_inventory_change(
			stock=stock,
			categoria='ENTRADA' if delta_fisico > 0 else 'SALIDA',
			tipo=movement_type,
			cantidad=note_item.cantidad,
			delta_fisico=delta_fisico * note_item.cantidad,
			delta_reservado=0,
			referencia=nota.numero,
			idempotency_key=f'{movement_type}-{nota.id}-{note_item.id}',
			observacion=nota.descripcion,
			invoice=nota.invoice,
			nota_ajuste=nota,
			nota_ajuste_item=note_item,
			creado_por=created_by,
		)


def _recalculate_invoice_balances(invoice):
	invoice.total_neto = _quantize_money(invoice.subtotal - invoice.total_creditos + invoice.total_debitos)
	invoice.saldo_cliente = _quantize_money(invoice.total_neto)
	invoice.save(update_fields=['total_creditos', 'total_debitos', 'total_neto', 'saldo_cliente', 'actualizada_en'])
	return invoice


@transaction.atomic
def crear_nota_ajuste_desde_invoice(
	*,
	invoice,
	tipo_documento,
	motivo,
	tipo_credito,
	descripcion,
	usuario,
	items_payload,
):
	tipo_documento = (tipo_documento or '').strip()
	motivo = (motivo or '').strip()
	tipo_credito = (tipo_credito or '').strip()
	descripcion = (descripcion or '').strip()
	if not tipo_documento:
		raise ValidationError(_('Select a note type to save the adjustment.'))
	if not motivo:
		raise ValidationError(_('Select a reason to save the adjustment.'))

	normalized_items = []
	for payload in items_payload or []:
		invoice_item = payload.get('invoice_item')
		quantity = int(payload.get('cantidad') or 0)
		unit_amount = _quantize_money(payload.get('monto_unitario') or '0')
		has_quantity = quantity > 0
		has_amount = unit_amount > 0
		if has_quantity and not has_amount:
			raise ValidationError(_('Enter a unit amount greater than zero for each selected adjustment item.'))
		if has_amount and not has_quantity:
			raise ValidationError(_('Enter a quantity greater than zero for each selected adjustment item.'))
		if not has_quantity and not has_amount:
			continue
		normalized_items.append({
			'invoice_item': invoice_item,
			'cantidad': quantity,
			'monto_unitario': unit_amount,
		})

	if not normalized_items:
		raise ValidationError(_('Add at least one item before saving the adjustment.'))

	inventario_estado = 'PENDIENTE' if tipo_documento == 'CREDITO' and tipo_credito == 'CREDIT_RETURN' else 'NO_APLICA'
	nota = NotaAjuste(
		invoice=invoice,
		tipo_documento=tipo_documento,
		motivo=motivo,
		tipo_credito=tipo_credito,
		descripcion=descripcion,
		inventario_estado=inventario_estado,
		creada_por=usuario,
	)
	nota.full_clean()
	nota.save()

	total = Decimal('0.00')
	for payload in normalized_items:
		invoice_item = payload.get('invoice_item')
		cantidad = int(payload.get('cantidad') or 0)
		monto_unitario = payload.get('monto_unitario')
		line_total = _quantize_money(monto_unitario * Decimal(str(cantidad)))
		NotaAjusteItem.objects.create(
			nota=nota,
			invoice_item=invoice_item,
			presentacion=_extract_note_presentacion(invoice_item),
			descripcion=getattr(invoice_item, 'producto_nombre', nota.descripcion or invoice.numero),
			cantidad=cantidad,
			monto_unitario=monto_unitario,
			total=line_total,
		)
		total += line_total

	nota.total = _quantize_money(total)
	nota.impacto_saldo = nota.total
	nota.save(update_fields=['total', 'impacto_saldo'])

	crear_notificacion_backoffice(
		titulo=_('Adjustment note %(number)s requires review') % {'number': nota.numero},
		mensaje=_('Adjustment note %(number)s is ready for BackOffice to approve or reject.') % {'number': nota.numero},
		tipo='NOTA_AJUSTE',
		url=f'/facturacion/backoffice/invoices/{invoice.id}/',
	)
	return nota


@transaction.atomic
def aprobar_nota_ajuste(*, nota, usuario):
	if nota.estado != 'BORRADOR':
		raise ValidationError(_('Only draft adjustment notes can be approved.'))

	invoice = nota.invoice
	if nota.tipo_documento == 'CREDITO':
		invoice.total_creditos = _quantize_money(invoice.total_creditos + nota.total)
		if nota.tipo_credito == 'CREDIT_RETURN':
			_apply_credit_note_inventory(nota=nota, movement_type='ENTRADA_NOTA_CREDITO', delta_fisico=1, created_by=usuario)
			nota.inventario_estado = 'PROCESADO'
	else:
		invoice.total_debitos = _quantize_money(invoice.total_debitos + nota.total)
		if nota.inventario_estado == 'PENDIENTE':
			nota.inventario_estado = 'NO_APLICA'

	_recalculate_invoice_balances(invoice)
	nota.estado = 'APROBADA'
	nota.aprobada_por = usuario
	nota.aprobada_en = timezone.now()
	nota.save(update_fields=['estado', 'aprobada_por', 'aprobada_en', 'inventario_estado'])
	return nota


@transaction.atomic
def anular_nota_ajuste(*, nota):
	if nota.estado == 'ANULADA':
		return nota

	invoice = nota.invoice
	if nota.estado == 'APROBADA':
		if nota.tipo_documento == 'CREDITO':
			invoice.total_creditos = _quantize_money(invoice.total_creditos - nota.total)
			if nota.inventario_estado == 'PROCESADO':
				_apply_credit_note_inventory(nota=nota, movement_type='REVERSO_NOTA_CREDITO', delta_fisico=-1)
		else:
			invoice.total_debitos = _quantize_money(invoice.total_debitos - nota.total)
		_recalculate_invoice_balances(invoice)

	nota.estado = 'ANULADA'
	nota.anulada_en = timezone.now()
	if nota.inventario_estado == 'PROCESADO':
		nota.inventario_estado = 'ANULADO'
	nota.save(update_fields=['estado', 'anulada_en', 'inventario_estado'])
	return nota


@transaction.atomic
def unlock_client_from_delivery(*, delivery, backoffice_user):
	cliente = delivery.invoice.cliente
	cliente.credit_hold = False
	cliente.save(update_fields=['credit_hold'])
	delivery.client_blocked_on_delivery = False
	delivery.client_unlocked_by = backoffice_user
	delivery.client_unlocked_at = timezone.now()
	delivery.save(update_fields=['client_blocked_on_delivery', 'client_unlocked_by', 'client_unlocked_at', 'updated_at'])
	return delivery