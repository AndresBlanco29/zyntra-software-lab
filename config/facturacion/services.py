import base64
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from config.notificaciones.models import crear_notificacion_backoffice, crear_notificacion_usuario
from config.inventario.services import procesar_nota_credito_inventario, revertir_nota_credito_inventario

from .models import Delivery, DeliveryEvidencePhoto, DeliveryNotificationLog, Invoice, InvoiceItem, NotaAjuste, NotaAjusteItem


logger = logging.getLogger(__name__)


def _notify_backoffice_driver_adjustment_note(nota):
	created_by = getattr(nota, 'creada_por', None)
	if created_by is None or getattr(created_by, 'role', '') != 'driver':
		return

	driver_name = created_by.get_full_name().strip() or created_by.username
	crear_notificacion_backoffice(
		titulo=_('Adjustment note %(note)s requires review') % {'note': nota.numero},
		mensaje=_('Driver %(driver)s created adjustment note %(note)s for invoice %(invoice)s. BackOffice must review it to approve or reject.') % {
			'driver': driver_name,
			'note': nota.numero,
			'invoice': nota.invoice.numero,
		},
		tipo='NOTA_AJUSTE',
		url=f'/facturacion/backoffice/invoices/{nota.invoice_id}/',
	)


def resolve_presentacion_suggested_unit_price(*, presentacion, base_case_price):
	base_price = _to_decimal(base_case_price).quantize(Decimal('0.01'))
	if not presentacion:
		return base_price

	configured_prices = sorted({
		Decimal(str(price or '0')).quantize(Decimal('0.01'))
		for price in (
			presentacion.precio_1,
			presentacion.precio_2,
			presentacion.precio_3,
			presentacion.precio_4,
			presentacion.precio_5,
		)
		if Decimal(str(price or '0')) > 0
	})

	suggested_case_price = base_price
	for configured_price in configured_prices:
		if configured_price > base_price:
			suggested_case_price = configured_price
			break
	else:
		if configured_prices:
			suggested_case_price = configured_prices[-1]

	if not getattr(presentacion, 'unidades', 0):
		return suggested_case_price
	return (suggested_case_price / Decimal(str(presentacion.unidades))).quantize(Decimal('0.01'))


def _to_decimal(value, default='0'):
	text = str(value if value is not None else default).strip().replace(',', '.')
	if not text:
		text = str(default)
	try:
		return Decimal(text)
	except (InvalidOperation, TypeError, ValueError):
		return Decimal(str(default))


def recalcular_invoice(invoice):
	subtotal = sum((item.subtotal for item in invoice.items.all()), Decimal('0.00'))
	total_creditos = sum((
		nota.total for nota in invoice.notas_ajuste.filter(estado='APROBADA', tipo_documento='CREDITO')
	), Decimal('0.00'))
	total_debitos = sum((
		nota.total for nota in invoice.notas_ajuste.filter(estado='APROBADA', tipo_documento='DEBITO')
	), Decimal('0.00'))
	total_neto = subtotal + total_debitos - total_creditos

	invoice.subtotal = subtotal
	invoice.total_creditos = total_creditos
	invoice.total_debitos = total_debitos
	invoice.total_neto = total_neto
	invoice.saldo_cliente = total_neto
	invoice.save(update_fields=['subtotal', 'total_creditos', 'total_debitos', 'total_neto', 'saldo_cliente', 'actualizada_en'])
	return invoice


def _build_delivery_snapshot(invoice):
	cliente = invoice.cliente
	return {
		'delivery_address': cliente.direccion,
		'delivery_city': cliente.ciudad,
		'delivery_state': cliente.estado,
		'delivery_postal_code': cliente.codigo_postal or '',
		'delivery_country': cliente.pais or 'USA',
	}


def ensure_delivery_for_invoice(invoice, *, estimated_delivery_at=None):
	if invoice.metodo_entrega != 'RUTA_DRIVER' or not invoice.driver_id:
		return None

	defaults = {
		'driver': invoice.driver,
		'estimated_delivery_at': estimated_delivery_at,
		**_build_delivery_snapshot(invoice),
	}
	delivery, created = Delivery.objects.get_or_create(invoice=invoice, defaults=defaults)
	if not created:
		updated_fields = []
		if delivery.driver_id != invoice.driver_id:
			delivery.driver = invoice.driver
			updated_fields.append('driver')
		for field_name, value in _build_delivery_snapshot(invoice).items():
			if getattr(delivery, field_name) != value:
				setattr(delivery, field_name, value)
				updated_fields.append(field_name)
		if estimated_delivery_at is not None and delivery.estimated_delivery_at != estimated_delivery_at:
			delivery.estimated_delivery_at = estimated_delivery_at
			updated_fields.append('estimated_delivery_at')
		if updated_fields:
			delivery.save(update_fields=updated_fields + ['updated_at'])
	return delivery


def build_google_maps_route_url(deliveries):
	addresses = [delivery.route_query_address for delivery in deliveries if delivery.route_query_address]
	if not addresses:
		raise ValidationError(_('No delivery addresses are available to build the route.'))
	params = {
		'api': 1,
		'travelmode': 'driving',
	}
	if len(addresses) == 1:
		params['destination'] = addresses[0]
		return f'https://www.google.com/maps/dir/?{urlencode(params)}'

	params['destination'] = addresses[-1]
	params['waypoints'] = '|'.join(addresses[:-1])
	return f'https://www.google.com/maps/dir/?{urlencode(params)}'


def _save_signature(delivery, signature_data_url):
	data_url = (signature_data_url or '').strip()
	if not data_url or ',' not in data_url:
		raise ValidationError(_('Customer signature is required to complete the delivery.'))
	header, encoded = data_url.split(',', 1)
	if ';base64' not in header:
		raise ValidationError(_('Invalid signature format.'))
	try:
		binary = base64.b64decode(encoded)
	except (ValueError, TypeError):
		raise ValidationError(_('Invalid signature payload.'))
	filename = f'{delivery.invoice.numero.lower()}-{timezone.now().strftime("%Y%m%d%H%M%S")}.png'
	delivery.firma_cliente.save(filename, ContentFile(binary), save=False)
	delivery.firma_recibida_en = timezone.now()


def _payment_details_from_payload(payload):
	return {
		'cheque_numero': (payload.get('cheque_numero') or '').strip(),
		'cheque_banco': (payload.get('cheque_banco') or '').strip(),
		'transferencia_referencia': (payload.get('transferencia_referencia') or '').strip(),
		'tarjeta_ultimos_4': (payload.get('tarjeta_ultimos_4') or '').strip(),
		'tarjeta_autorizacion': (payload.get('tarjeta_autorizacion') or '').strip(),
		'zelle_referencia': (payload.get('zelle_referencia') or '').strip(),
		'zelle_remitente': (payload.get('zelle_remitente') or '').strip(),
		'ach_referencia': (payload.get('ach_referencia') or '').strip(),
		'ach_cuenta_ultimos_4': (payload.get('ach_cuenta_ultimos_4') or '').strip(),
	}


def _record_delivery_notification(delivery, *, channel, status, target='', message='', error_message=''):
	return DeliveryNotificationLog.objects.create(
		delivery=delivery,
		channel=channel,
		status=status,
		target=target,
		message=message,
		error_message=error_message,
	)


def _normalize_phone_number(raw_phone):
	digits = ''.join(character for character in (raw_phone or '') if character.isdigit())
	if not digits:
		return ''
	if len(digits) == 10:
		return f'+1{digits}'
	if digits.startswith('1') and len(digits) == 11:
		return f'+{digits}'
	if raw_phone.startswith('+'):
		return raw_phone
	return f'+{digits}'


def _delivery_notification_message(delivery):
	status = _('paid') if delivery.estado_pago == 'PAGADO' else _('unpaid')
	return _(
		'Your order %(invoice)s was delivered. Delivery status: %(status)s. Recipient: %(recipient)s.'
	) % {
		'invoice': delivery.invoice.numero,
		'status': status,
		'recipient': delivery.recibido_por,
	}


def _send_client_delivery_notifications(delivery):
	cliente = delivery.invoice.cliente
	usuario = cliente.usuario
	message = _delivery_notification_message(delivery)
	invoice_url = f'{settings.APP_BASE_URL}/facturacion/driver/deliveries/{delivery.id}/' if settings.APP_BASE_URL else f'/facturacion/driver/deliveries/{delivery.id}/'
	email_target = getattr(usuario, 'email', '') or ''
	if email_target:
		try:
			send_mail(
				subject=f'{delivery.invoice.numero} - {_("Delivery confirmation")}',
				message=f'{message}\n{invoice_url}',
				from_email=settings.DEFAULT_FROM_EMAIL,
				recipient_list=[email_target],
				fail_silently=False,
			)
		except Exception as exc:
			logger.exception('Email delivery notification failed for %s', delivery.invoice.numero)
			_record_delivery_notification(delivery, channel='EMAIL', status='FAILED', target=email_target, message=message, error_message=str(exc))
		else:
			_record_delivery_notification(delivery, channel='EMAIL', status='SENT', target=email_target, message=message)
	else:
		_record_delivery_notification(delivery, channel='EMAIL', status='SKIPPED', message=message, error_message=str(_('Customer email is missing.')))

	phone = _normalize_phone_number(cliente.telefono)
	if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and phone:
		client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
		if settings.TWILIO_SMS_FROM:
			try:
				client.messages.create(body=message, from_=settings.TWILIO_SMS_FROM, to=phone)
			except TwilioRestException as exc:
				_record_delivery_notification(delivery, channel='SMS', status='FAILED', target=phone, message=message, error_message=str(exc))
			else:
				_record_delivery_notification(delivery, channel='SMS', status='SENT', target=phone, message=message)
		else:
			_record_delivery_notification(delivery, channel='SMS', status='SKIPPED', target=phone, message=message, error_message=str(_('TWILIO_SMS_FROM is not configured.')))

		if settings.TWILIO_WHATSAPP_FROM:
			whatsapp_from = settings.TWILIO_WHATSAPP_FROM
			if not whatsapp_from.startswith('whatsapp:'):
				whatsapp_from = f'whatsapp:{whatsapp_from}'
			try:
				client.messages.create(body=f'{message}\n{invoice_url}', from_=whatsapp_from, to=f'whatsapp:{phone}')
			except TwilioRestException as exc:
				_record_delivery_notification(delivery, channel='WHATSAPP', status='FAILED', target=phone, message=message, error_message=str(exc))
			else:
				_record_delivery_notification(delivery, channel='WHATSAPP', status='SENT', target=phone, message=message)
		else:
			_record_delivery_notification(delivery, channel='WHATSAPP', status='SKIPPED', target=phone, message=message, error_message=str(_('TWILIO_WHATSAPP_FROM is not configured.')))
	else:
		error = _('Customer phone or Twilio credentials are missing.')
		_record_delivery_notification(delivery, channel='SMS', status='SKIPPED', target=phone, message=message, error_message=str(error))
		_record_delivery_notification(delivery, channel='WHATSAPP', status='SKIPPED', target=phone, message=message, error_message=str(error))

	delivery.notifications_sent_at = timezone.now()
	delivery.save(update_fields=['notifications_sent_at', 'updated_at'])


@transaction.atomic
def generar_invoice_desde_picking(*, pedido, metodo_entrega, driver, usuario, suggested_unit_prices=None, estimated_delivery_at=None):
	if pedido.estado != 'VERIFICADO_AJUSTADO':
		raise ValidationError(_('The order must be verified and adjusted before generating an invoice.'))
	if pedido.picking_bloqueado:
		raise ValidationError(_('The order remains blocked by an unresolved selector note.'))
	if hasattr(pedido, 'invoice'):
		raise ValidationError(_('An invoice has already been generated for this order.'))
	if metodo_entrega == 'RUTA_DRIVER' and (not driver or getattr(driver, 'role', '') != 'driver'):
		raise ValidationError(_('Route delivery requires an active driver assignment.'))
	if metodo_entrega != 'RUTA_DRIVER':
		driver = None
		estimated_delivery_at = None

	suggested_unit_prices = suggested_unit_prices or {}

	items = list(pedido.items.select_related('presentacion__producto').all())
	facturables = [item for item in items if item.cantidad > 0]
	if not facturables:
		raise ValidationError(_('The order must contain at least one verified quantity greater than zero.'))

	invoice = Invoice(
		pedido=pedido,
		cliente=pedido.cliente,
		metodo_entrega=metodo_entrega,
		driver=driver,
		creada_por=usuario,
	)
	invoice.full_clean()
	invoice.save()

	for item in facturables:
		suggested_unit_price = suggested_unit_prices.get(item.id)
		if suggested_unit_price is None:
			suggested_unit_price = resolve_presentacion_suggested_unit_price(
				presentacion=item.presentacion,
				base_case_price=item.precio,
			)
		InvoiceItem.objects.create(
			invoice=invoice,
			pedido_item=item,
			presentacion=item.presentacion,
			producto_nombre=item.presentacion.producto.nombre,
			presentacion_nombre=item.presentacion.nombre,
			cantidad_facturada=item.cantidad,
			precio_unitario=item.precio,
			precio_venta_sugerido_unitario=suggested_unit_price,
			subtotal=item.subtotal,
		)

	recalcular_invoice(invoice)

	pedido.estado = 'INVOICE_GENERADA'
	pedido.save(update_fields=['estado', 'actualizada_en'])

	timestamp = timezone.now()
	invoice.despachador_notificado = True
	invoice.notificado_en = timestamp
	invoice.save(update_fields=['despachador_notificado', 'notificado_en', 'actualizada_en'])

	crear_notificacion_backoffice(
		titulo=f'{_("Invoice generated")} #{invoice.numero}',
		mensaje=_("Invoice %(invoice)s is ready for dispatch planning.") % {'invoice': invoice.numero},
		tipo='PEDIDO',
		url=f'/facturacion/backoffice/invoices/{invoice.id}/',
	)
	if driver:
		crear_notificacion_usuario(
			usuario=driver,
			titulo=f'{_("New route assigned")} {invoice.numero}',
			mensaje=_("Invoice %(invoice)s was assigned to you for route delivery.") % {'invoice': invoice.numero},
			tipo='PEDIDO',
		)
		ensure_delivery_for_invoice(invoice, estimated_delivery_at=estimated_delivery_at)

	return invoice


@transaction.atomic
def crear_nota_ajuste_desde_invoice(*, invoice, tipo_documento, motivo, tipo_credito, descripcion, usuario, items_payload):
	if invoice.estado != 'GENERADA':
		raise ValidationError(_('You can only create adjustment notes for active invoices.'))

	lineas = []
	for payload in items_payload:
		invoice_item = payload['invoice_item']
		cantidad = int(payload['cantidad'])
		if cantidad <= 0:
			continue
		if cantidad > invoice_item.cantidad_facturada:
			raise ValidationError(_('Adjustment quantity cannot exceed the invoiced quantity.'))
		monto_unitario = _to_decimal(payload.get('monto_unitario') or invoice_item.precio_unitario)
		if monto_unitario < 0:
			raise ValidationError(_('Adjustment amounts cannot be negative.'))
		lineas.append((invoice_item, cantidad, monto_unitario, monto_unitario * cantidad))

	if not lineas:
		raise ValidationError(_('Add at least one adjustment line before saving the note.'))

	nota = NotaAjuste(
		invoice=invoice,
		tipo_documento=tipo_documento,
		motivo=motivo,
		tipo_credito=tipo_credito if tipo_documento == 'CREDITO' else '',
		descripcion=(descripcion or '').strip(),
		creada_por=usuario,
	)
	nota.full_clean()
	nota.save()

	total = Decimal('0.00')
	for invoice_item, cantidad, monto_unitario, subtotal in lineas:
		NotaAjusteItem.objects.create(
			nota=nota,
			invoice_item=invoice_item,
			presentacion=invoice_item.presentacion,
			descripcion=f'{invoice_item.producto_nombre} - {invoice_item.presentacion_nombre}',
			cantidad=cantidad,
			monto_unitario=monto_unitario,
			total=subtotal,
		)
		total += subtotal

	nota.total = total
	nota.impacto_saldo = total if tipo_documento == 'CREDITO' else total
	nota.inventario_estado = 'PENDIENTE' if tipo_documento == 'CREDITO' and tipo_credito == 'CREDIT_RETURN' else 'NO_APLICA'
	nota.save(update_fields=['total', 'impacto_saldo', 'inventario_estado'])
	_notify_backoffice_driver_adjustment_note(nota)
	return nota


@transaction.atomic
def aprobar_nota_ajuste(*, nota, usuario):
	if nota.estado != 'BORRADOR':
		raise ValidationError(_('Only draft notes can be approved.'))
	if not nota.items.exists():
		raise ValidationError(_('The note must contain at least one item before approval.'))

	nota.estado = 'APROBADA'
	nota.aprobada_por = usuario
	nota.aprobada_en = timezone.now()
	processed_inventory = procesar_nota_credito_inventario(nota=nota, creado_por=usuario)
	nota.inventario_estado = 'PROCESADO' if processed_inventory else 'NO_APLICA'
	nota.save(update_fields=['estado', 'aprobada_por', 'aprobada_en', 'inventario_estado'])

	recalcular_invoice(nota.invoice)
	return nota


@transaction.atomic
def anular_nota_ajuste(*, nota):
	if nota.estado == 'ANULADA':
		raise ValidationError(_('This note has already been cancelled.'))
	if nota.estado == 'APROBADA' and nota.inventario_estado == 'PROCESADO':
		revertir_nota_credito_inventario(nota=nota, creado_por=getattr(nota, 'aprobada_por', None))

	nota.estado = 'ANULADA'
	nota.anulada_en = timezone.now()
	nota.inventario_estado = 'ANULADO' if nota.tipo_documento == 'CREDITO' and nota.tipo_credito == 'CREDIT_RETURN' else 'NO_APLICA'
	nota.save(update_fields=['estado', 'anulada_en', 'inventario_estado'])
	recalcular_invoice(nota.invoice)
	return nota


@transaction.atomic
def start_delivery_route(*, delivery, driver_user):
	if delivery.driver_id != getattr(driver_user, 'id', None):
		raise ValidationError(_('You can only start routes assigned to you.'))
	if delivery.is_completed:
		raise ValidationError(_('This delivery has already been completed.'))
	if delivery.estado == 'ASIGNADA':
		delivery.estado = 'EN_RUTA'
		delivery.route_started_at = timezone.now()
		delivery.save(update_fields=['estado', 'route_started_at', 'updated_at'])
	return delivery


@transaction.atomic
def complete_driver_delivery(*, delivery, driver_user, payload, evidence_files):
	if delivery.driver_id != getattr(driver_user, 'id', None):
		raise ValidationError(_('You can only complete deliveries assigned to you.'))
	if delivery.is_completed:
		raise ValidationError(_('This delivery has already been completed.'))

	estado_pago = (payload.get('estado_pago') or '').strip()
	if estado_pago not in {'PAGADO', 'NO_PAGADO'}:
		raise ValidationError(_('Select whether the delivery was paid or not paid.'))

	delivery.estado_pago = estado_pago
	delivery.recibido_por = (payload.get('recibido_por') or '').strip()
	delivery.notas_driver = (payload.get('notas_driver') or '').strip()
	delivery.motivo_no_pago = (payload.get('motivo_no_pago') or '').strip()
	delivery.metodo_pago = (payload.get('metodo_pago') or '').strip() if estado_pago == 'PAGADO' else ''
	delivery.monto_pagado = _to_decimal(payload.get('monto_pagado') or delivery.invoice.saldo_cliente)
	for field_name, value in _payment_details_from_payload(payload).items():
		setattr(delivery, field_name, value if estado_pago == 'PAGADO' else '')
	_save_signature(delivery, payload.get('firma_cliente_data'))

	if estado_pago == 'PAGADO':
		delivery.estado = 'ENTREGADA_PAGADA'
		delivery.client_blocked_on_delivery = False
	else:
		if not evidence_files:
			raise ValidationError(_('At least one evidence photo is required when the customer does not pay.'))
		delivery.estado = 'ENTREGADA_SIN_PAGO'
		delivery.monto_pagado = Decimal('0.00')
		delivery.client_blocked_on_delivery = True

	delivery.delivered_at = timezone.now()
	delivery.full_clean()
	delivery.save()

	if evidence_files:
		for uploaded_file in evidence_files:
			DeliveryEvidencePhoto.objects.create(delivery=delivery, image=uploaded_file)

	cliente = delivery.invoice.cliente
	if estado_pago == 'NO_PAGADO':
		cliente.credit_hold = True
		cliente.save(update_fields=['credit_hold'])

	_send_client_delivery_notifications(delivery)
	crear_notificacion_backoffice(
		titulo=f'{_("Delivery completed")} {delivery.invoice.numero}',
		mensaje=_("Driver %(driver)s completed %(invoice)s with payment status %(status)s.") % {
			'driver': driver_user.get_full_name() or driver_user.username,
			'invoice': delivery.invoice.numero,
			'status': delivery.get_estado_pago_display(),
		},
		tipo='PEDIDO',
		url=f'/facturacion/backoffice/invoices/{delivery.invoice_id}/',
	)
	return delivery


@transaction.atomic
def unlock_client_from_delivery(*, delivery, backoffice_user):
	cliente = delivery.invoice.cliente
	cliente.credit_hold = False
	cliente.save(update_fields=['credit_hold'])
	delivery.client_unlocked_by = backoffice_user
	delivery.client_unlocked_at = timezone.now()
	delivery.save(update_fields=['client_unlocked_by', 'client_unlocked_at', 'updated_at'])
	return delivery