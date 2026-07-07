import base64
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from config.auditoria.models import AuditLog
from config.inventario.services import _apply_fractional_inventory_change, _apply_inventory_change, _lock_fractional_stock_records, _lock_stock_records, aplicar_inventario_pendiente_pedido, aplicar_verificacion_picking_inventario, restaurar_inventario_por_anulacion_factura
from config.notificaciones.models import crear_notificacion_backoffice
from config.pedidos.services import crear_pedido_desde_items
from config.productos.models import Presentacion

from .models import Delivery, DeliveryEvidencePhoto, DeliveryNotificationLog, DeliveryPayment, FacturacionRegistroAnulacion, Invoice, InvoiceItem, NotaAjuste, NotaAjusteAplicacion, NotaAjusteItem


DEFAULT_SUGGESTED_PROFIT_PERCENTAGE = Decimal('30.00')


def _ensure_quickbooks_not_locked(*, invoice=None, nota=None):
	from config.integrations.quickbooks.sync import is_sync_locked

	if nota is not None and is_sync_locked(nota):
		raise ValidationError(_('Adjustment note %(note)s is locked because it is already synced with QuickBooks.') % {'note': nota.numero})
	if invoice is not None and is_sync_locked(invoice):
		raise ValidationError(_('Invoice %(invoice)s is locked because it is already synced with QuickBooks.') % {'invoice': invoice.numero})


def resolve_invoice_payment_base_date(invoice):
	delivery = getattr(invoice, 'delivery', None)
	if delivery is not None and delivery.estimated_delivery_at:
		return timezone.localtime(delivery.estimated_delivery_at).date()
	return timezone.localtime(invoice.creada_en).date()


def resolve_invoice_payment_due_date(invoice):
	return invoice.cliente.get_payment_due_date(resolve_invoice_payment_base_date(invoice))


def resolve_invoice_item_case_weight(item):
	from config.core.shipment_summary import resolve_line_case_weight

	return resolve_line_case_weight(
		case_weight=item.peso_por_caja,
		presentacion=getattr(item, 'presentacion', None),
	)


PRODUCT_CREDIT_NOTE_TYPES = frozenset({'CREDIT_RETURN', 'CREDIT_DUMP'})
DRIVER_INVENTORY_RETURN_CREDIT_REASONS = frozenset({'DEFECT'})


def resolve_driver_credit_type_from_motivo(motivo):
	"""Factory defects can return to inventory; damaged or missing items cannot."""
	if (motivo or '').strip().upper() in DRIVER_INVENTORY_RETURN_CREDIT_REASONS:
		return 'CREDIT_RETURN'
	return 'CREDIT_DUMP'


def _invoice_item_units_per_package(invoice_item):
	return max(int(getattr(getattr(invoice_item, 'presentacion', None), 'unidades', 0) or 0), 1)


def _note_item_returned_units(note_item):
	if note_item.contenido_fraccionado:
		return int(note_item.cantidad or 0)
	presentation = note_item.presentacion
	invoice_item = note_item.invoice_item
	units_per_package = max(
		int(getattr(presentation, 'unidades', 0) or 0),
		_invoice_item_units_per_package(invoice_item) if invoice_item is not None else 1,
		1,
	)
	return int(note_item.cantidad or 0) * units_per_package


def build_invoice_item_returned_units_map(invoice):
	returned_units = {}
	note_items = NotaAjusteItem.objects.filter(
		invoice_item__invoice=invoice,
		nota__estado='APROBADA',
		nota__tipo_documento='CREDITO',
		nota__tipo_ajuste='PRODUCTO',
		nota__tipo_credito__in=PRODUCT_CREDIT_NOTE_TYPES,
	).select_related('presentacion', 'invoice_item__presentacion')
	for note_item in note_items:
		if not note_item.invoice_item_id:
			continue
		returned_units[note_item.invoice_item_id] = returned_units.get(note_item.invoice_item_id, 0) + _note_item_returned_units(note_item)
	return returned_units


def resolve_invoice_item_returned_packages(invoice_item, returned_units_map=None):
	returned_units = (returned_units_map or {}).get(invoice_item.id, 0)
	if returned_units <= 0:
		return 0
	units_per_package = _invoice_item_units_per_package(invoice_item)
	return min(int(invoice_item.cantidad_facturada or 0), returned_units // units_per_package)


def resolve_invoice_item_net_dispatched_quantity(invoice_item, returned_units_map=None):
	if returned_units_map is None and getattr(invoice_item, 'invoice_id', None):
		returned_units_map = build_invoice_item_returned_units_map(invoice_item.invoice)
	returned_packages = resolve_invoice_item_returned_packages(invoice_item, returned_units_map)
	return max(0, int(invoice_item.cantidad_facturada or 0) - returned_packages)


def resolve_invoice_item_returnable_units(invoice_item, returned_units_map=None):
	if returned_units_map is None and getattr(invoice_item, 'invoice_id', None):
		returned_units_map = build_invoice_item_returned_units_map(invoice_item.invoice)
	max_units = int(invoice_item.cantidad_facturada or 0) * _invoice_item_units_per_package(invoice_item)
	already_returned = (returned_units_map or {}).get(invoice_item.id, 0)
	return max(0, max_units - already_returned)


def attach_invoice_item_net_dispatched_quantities(invoice, items):
	returned_units_map = build_invoice_item_returned_units_map(invoice)
	for item in items:
		item._cantidad_despachada_neta = resolve_invoice_item_net_dispatched_quantity(item, returned_units_map)
	return items


def build_invoice_shipment_summary(invoice):
	from config.core.shipment_summary import build_shipment_summary_from_lines

	returned_units_map = build_invoice_item_returned_units_map(invoice)
	return build_shipment_summary_from_lines([
		{
			'quantity': resolve_invoice_item_net_dispatched_quantity(item, returned_units_map),
			'case_weight': item.peso_por_caja,
			'presentacion': getattr(item, 'presentacion', None),
		}
		for item in invoice.items.all()
	])


def _to_decimal(value, default='0'):
	try:
		return Decimal(str(value if value is not None else default))
	except (InvalidOperation, TypeError, ValueError):
		return Decimal(str(default))


def _quantize_money(value):
	return _to_decimal(value, '0').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _parse_line_discount_percentage(value):
	text = str(value or '').strip().replace(',', '.')
	if not text:
		return Decimal('0.00')
	try:
		parsed = Decimal(text)
	except (InvalidOperation, TypeError, ValueError):
		raise ValidationError(_('Line discount must be a valid number.'))
	if parsed < 0:
		raise ValidationError(_('Line discount cannot be negative.'))
	if parsed > 100:
		raise ValidationError(_('Line discount cannot exceed 100%.'))
	return parsed.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _calculate_discounted_unit_price(list_price, discount_percentage):
	list_price = _quantize_money(list_price)
	discount_percentage = _parse_line_discount_percentage(discount_percentage)
	if discount_percentage <= 0:
		return list_price
	factor = Decimal('1') - (discount_percentage / Decimal('100'))
	return _quantize_money(list_price * factor)


def _clamp_non_negative_money(value):
	return max(_quantize_money(value), Decimal('0.00'))


def _normalize_uploaded_file(uploaded_file):
	if uploaded_file is None:
		return None
	name = str(getattr(uploaded_file, 'name', '') or '').strip()
	size = getattr(uploaded_file, 'size', None)
	if not name:
		return None
	if size is not None and int(size) <= 0:
		return None
	if size is None:
		stream = getattr(uploaded_file, 'file', uploaded_file)
		if hasattr(stream, 'tell') and hasattr(stream, 'seek'):
			current_position = stream.tell()
			stream.seek(0, 2)
			resolved_size = stream.tell()
			stream.seek(current_position)
			if int(resolved_size) <= 0:
				return None
		elif hasattr(uploaded_file, 'read'):
			content = uploaded_file.read()
			if hasattr(uploaded_file, 'seek'):
				uploaded_file.seek(0)
			if not content:
				return None
	return uploaded_file


def _normalize_uploaded_files(uploaded_files):
	return [normalized_file for normalized_file in (_normalize_uploaded_file(uploaded_file) for uploaded_file in (uploaded_files or [])) if normalized_file is not None]


def _rewind_uploaded_file(uploaded_file):
	if uploaded_file is None:
		return None
	stream = getattr(uploaded_file, 'file', uploaded_file)
	if hasattr(stream, 'seek'):
		stream.seek(0)
	elif hasattr(uploaded_file, 'seek'):
		uploaded_file.seek(0)
	return uploaded_file


def _calculate_suggested_unit_price_from_profit(base_unit_price, profit_percentage=DEFAULT_SUGGESTED_PROFIT_PERCENTAGE):
	base_unit_decimal = _quantize_money(base_unit_price)
	profit_decimal = _to_decimal(profit_percentage, DEFAULT_SUGGESTED_PROFIT_PERCENTAGE)
	divisor = Decimal('1') - (profit_decimal / Decimal('100'))
	if divisor <= 0:
		return base_unit_decimal
	return (base_unit_decimal / divisor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


ALLOWED_PICKING_INVOICE_DELIVERY_METHODS = frozenset({'RUTA_DRIVER', 'CUSTOMER_PICK_UP'})
ALLOWED_DIRECT_INVOICE_DELIVERY_METHODS = frozenset({'CUSTOMER_PICK_UP', 'RUTA_DRIVER'})


def _validate_invoice_generation(pedido, metodo_entrega, driver):
	if pedido.estado != 'VERIFICADO_AJUSTADO':
		raise ValidationError(_('The invoice can only be generated from a verified and adjusted picking order.'))
	if pedido.picking_bloqueado:
		raise ValidationError(_('The order is blocked by an unresolved selector note.'))
	if hasattr(pedido, 'invoice'):
		raise ValidationError(_('This sales order already has an invoice.'))
	if metodo_entrega not in ALLOWED_PICKING_INVOICE_DELIVERY_METHODS:
		raise ValidationError(_('Only customer pickup or route delivery can be selected when generating an invoice.'))
	if metodo_entrega == 'RUTA_DRIVER' and driver is None:
		raise ValidationError(_('A driver is required for route deliveries.'))
	if metodo_entrega == 'CUSTOMER_PICK_UP' and driver is not None:
		raise ValidationError(_('Driver assignment is not allowed for customer pickup invoices.'))
	if driver is not None and getattr(driver, 'role', '') != 'driver':
		raise ValidationError(_('Only users with driver role can be assigned.'))


def _ensure_picking_inventory_applied_for_invoice(*, pedido, usuario):
	aplicar_inventario_pendiente_pedido(pedido=pedido, creado_por=usuario)
	pending_items = [
		item
		for item in pedido.items.select_related('presentacion__producto')
		if int(item.cantidad or 0) > int(item.cantidad_inventario_aplicada or 0)
	]
	if not pending_items:
		return
	product_labels = ', '.join(
		f'{item.presentacion.producto.nombre} - {item.presentacion.nombre}'
		for item in pending_items[:3]
	)
	ellipsis = '...' if len(pending_items) > 3 else ''
	raise ValidationError(
		_('Physical stock could not be applied for %(count)s invoiced item(s): %(products)s%(ellipsis)s. Review inventory before generating the invoice.') % {
			'count': len(pending_items),
			'products': product_labels,
			'ellipsis': ellipsis,
		}
	)


def resolve_presentacion_suggested_unit_price(*, presentacion, base_case_price):
	case_price = _quantize_money(base_case_price)
	units = max(int(getattr(presentacion, 'unidades', 0) or 0), 1) if presentacion else 1
	base_unit_price = (case_price / Decimal(str(units))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
	return _calculate_suggested_unit_price_from_profit(base_unit_price)


def _apply_customer_balance_delta(*, cliente, delta):
	cliente.balance = _quantize_money(Decimal(str(cliente.balance or '0.00')) + Decimal(str(delta or '0.00')))
	cliente.save(update_fields=['balance'])
	return cliente.balance


def _operational_open_invoice_outstanding_queryset(*, cliente):
	from django.db.models import Q

	return Invoice.objects.filter(
		cliente=cliente,
		estado='GENERADA',
		saldo_cliente__gt=0,
	).filter(
		Q(metodo_entrega='CUSTOMER_PICK_UP') | Q(delivery__estado_pago='NO_PAGADO')
	)


def _sum_operational_open_invoice_outstanding(*, cliente):
	from django.db.models import Sum

	total = (
		_operational_open_invoice_outstanding_queryset(cliente=cliente).aggregate(total=Sum('saldo_cliente'))['total']
		or Decimal('0.00')
	)
	return _quantize_money(total)


def _invoice_operational_outstanding(invoice):
	if invoice.estado != 'GENERADA':
		return Decimal('0.00')
	outstanding = _quantize_money(invoice.saldo_cliente)
	if outstanding <= 0:
		return Decimal('0.00')
	if invoice.metodo_entrega == 'CUSTOMER_PICK_UP':
		return outstanding
	delivery = getattr(invoice, 'delivery', None)
	if delivery is not None and delivery.estado_pago == 'NO_PAGADO':
		return outstanding
	return Decimal('0.00')


def _customer_has_open_invoice_saldo(*, cliente):
	return Invoice.objects.filter(cliente=cliente, estado='GENERADA', saldo_cliente__gt=0).exists()


def annotate_clientes_open_invoice_balance(queryset):
	from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum
	from django.db.models.functions import Coalesce

	open_invoice_subquery = (
		Invoice.objects.filter(
			cliente_id=OuterRef('pk'),
			estado='GENERADA',
			saldo_cliente__gt=0,
		)
		.filter(Q(metodo_entrega='CUSTOMER_PICK_UP') | Q(delivery__estado_pago='NO_PAGADO'))
		.values('cliente_id')
		.annotate(total=Sum('saldo_cliente'))
		.values('total')
	)
	return queryset.annotate(
		open_invoice_balance=Coalesce(
			Subquery(open_invoice_subquery, output_field=DecimalField(max_digits=12, decimal_places=2)),
			Decimal('0.00'),
		),
	)


def _customer_stored_due_balance_is_trusted(cliente):
    qb_id = str(getattr(cliente, 'quickbooks_id', None) or '').strip()
    if not qb_id:
        return False
    return str(getattr(cliente, 'sync_status', '') or '').upper() == 'SYNCED'


def resolve_customer_amount_owed(*, cliente, invoice=None):
	stored_due = _quantize_money(cliente.due_balance)
	if invoice is not None:
		invoice_operational = _invoice_operational_outstanding(invoice)
		if invoice_operational > 0:
			return _quantize_money(stored_due + invoice_operational)
		return stored_due

	operational_open = getattr(cliente, 'open_invoice_balance', None)
	if operational_open is None:
		operational_open = _sum_operational_open_invoice_outstanding(cliente=cliente)
	else:
		operational_open = _quantize_money(operational_open)

	if operational_open > 0:
		return _quantize_money(stored_due + operational_open)

	if (
		not _customer_has_open_invoice_saldo(cliente=cliente)
		and stored_due > Decimal('1000.00')
		and not _customer_stored_due_balance_is_trusted(cliente)
	):
		return Decimal('0.00')

	return stored_due


def resolve_customer_overdue_balance(*, cliente):
	"""Sum of open invoice balances that are already past due (excludes not-yet-due invoices)."""
	from config.clientes.balance_summary import build_customer_balance_summary

	return build_customer_balance_summary(cliente=cliente).overdue_balance


def resolve_customer_open_balance(*, cliente):
	"""Total open invoice balance for the customer (overdue + not yet due)."""
	from config.clientes.balance_summary import build_customer_balance_summary

	return build_customer_balance_summary(cliente=cliente).total_open_balance


def _calculate_invoice_totals(*, subtotal, credito_cliente_aplicado=Decimal('0.00'), total_creditos=Decimal('0.00'), total_debitos=Decimal('0.00'), total_pagado=Decimal('0.00')):
	subtotal = _quantize_money(subtotal)
	credito_cliente_aplicado = _clamp_non_negative_money(credito_cliente_aplicado)
	total_creditos = _clamp_non_negative_money(total_creditos)
	total_debitos = _clamp_non_negative_money(total_debitos)
	total_pagado = _clamp_non_negative_money(total_pagado)
	total_neto = _quantize_money(subtotal - credito_cliente_aplicado - total_creditos + total_debitos)
	return {
		'total_neto': total_neto,
		'saldo_cliente': _clamp_non_negative_money(total_neto - total_pagado),
	}


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


@transaction.atomic
def ensure_customer_pickup_delivery_for_invoice(invoice):
	if invoice.metodo_entrega != 'CUSTOMER_PICK_UP':
		return None
	if hasattr(invoice, 'delivery'):
		delivery = invoice.delivery
		if not delivery.is_customer_pickup:
			delivery.is_customer_pickup = True
			delivery.driver = None
			delivery.save(update_fields=['is_customer_pickup', 'driver', 'updated_at'])
		return delivery
	cliente = invoice.cliente
	return Delivery.objects.create(
		invoice=invoice,
		driver=None,
		is_customer_pickup=True,
		delivery_address=cliente.direccion or _('Customer pick up'),
		delivery_city=cliente.ciudad or '',
		delivery_state=cliente.estado or '',
		delivery_postal_code=cliente.codigo_postal or '',
		delivery_country=cliente.pais or 'USA',
	)


def _finalize_delivery_completion(
	*,
	delivery,
	acting_user,
	payload,
	evidence_files,
	estado_pago,
	recibido_por,
	motivo_no_pago,
	signature_file,
	payment_details,
	completed_by=None,
):
	delivery.estado_pago = estado_pago
	delivery.metodo_pago = payment_details['metodo_pago'] if payment_details else ''
	delivery.monto_pagado = payment_details['monto_pagado'] if payment_details else Decimal('0.00')
	delivery.monto_pagado_cash = payment_details['monto_pagado_cash'] if payment_details else Decimal('0.00')
	delivery.monto_pagado_cheque = payment_details['monto_pagado_cheque'] if payment_details else Decimal('0.00')
	delivery.recibido_por = recibido_por
	delivery.motivo_no_pago = motivo_no_pago if estado_pago == 'NO_PAGADO' else ''
	delivery.notas_driver = (payload.get('notas_driver') or payload.get('notas_pickup') or '').strip()
	delivery.firma_cliente = signature_file
	delivery.firma_recibida_en = timezone.now()
	delivery.delivered_at = timezone.now()
	delivery.notifications_sent_at = timezone.now()
	delivery.transferencia_referencia = payment_details['transferencia_referencia'] if payment_details else ''
	delivery.tarjeta_ultimos_4 = payment_details['tarjeta_ultimos_4'] if payment_details else ''
	delivery.tarjeta_autorizacion = payment_details['tarjeta_autorizacion'] if payment_details else ''
	delivery.zelle_referencia = payment_details['zelle_referencia'] if payment_details else ''
	delivery.zelle_remitente = payment_details['zelle_remitente'] if payment_details else ''
	delivery.ach_referencia = payment_details['ach_referencia'] if payment_details else ''
	delivery.ach_cuenta_ultimos_4 = payment_details['ach_cuenta_ultimos_4'] if payment_details else ''
	delivery.cheque_numero = payment_details['cheque_numero'] if payment_details else ''
	delivery.cheque_banco = payment_details['cheque_banco'] if payment_details else ''
	if payment_details and payment_details['cheque_imagen'] is not None:
		delivery.cheque_imagen = payment_details['cheque_imagen']
	elif not payment_details:
		delivery.cheque_imagen = None
	if completed_by is not None:
		delivery.completed_by = completed_by
	if estado_pago == 'PAGADO':
		delivery.estado = 'ENTREGADA_PAGADA'
		delivery.client_blocked_on_delivery = False
		delivery.invoice.cliente.credit_hold = False
		delivery.invoice.cliente.save(update_fields=['credit_hold'])
		_create_delivery_notification_logs(delivery, 'SENT')
	else:
		delivery.estado = 'ENTREGADA_SIN_PAGO'
		delivery.client_blocked_on_delivery = True
		delivery.monto_pagado_cash = Decimal('0.00')
		delivery.monto_pagado_cheque = Decimal('0.00')
		delivery.transferencia_referencia = ''
		delivery.tarjeta_ultimos_4 = ''
		delivery.tarjeta_autorizacion = ''
		delivery.zelle_referencia = ''
		delivery.zelle_remitente = ''
		delivery.ach_referencia = ''
		delivery.ach_cuenta_ultimos_4 = ''
		delivery.invoice.cliente.credit_hold = True
		delivery.invoice.cliente.save(update_fields=['credit_hold'])
		_create_delivery_notification_logs(delivery, 'FAILED')

	delivery.save()
	delivery.payments.all().delete()
	if payment_details:
		for entry in payment_details['entries']:
			payment_entry = entry.copy()
			payment_entry['cheque_imagen'] = _rewind_uploaded_file(payment_entry.get('cheque_imagen'))
			DeliveryPayment.objects.create(delivery=delivery, **payment_entry)
	_recalculate_invoice_balances(delivery.invoice)
	for normalized_file in evidence_files:
		DeliveryEvidencePhoto.objects.create(delivery=delivery, image=normalized_file)

	pedido = delivery.invoice.pedido
	pedido.estado = 'DESPACHADO'
	pedido.save(update_fields=['estado', 'actualizada_en'])
	from config.auditoria.business_events import log_business_event
	log_business_event(
		acting_user,
		action_label=_('Completed delivery for invoice %(invoice)s') % {'invoice': delivery.invoice.numero},
		action_category=AuditLog.CATEGORY_ACTION,
		entity_type='Delivery',
		entity_id=str(delivery.id),
		entity_label=delivery.invoice.numero,
		metadata={
			'invoice_id': delivery.invoice_id,
			'pedido_id': pedido.id if pedido else None,
			'estado': delivery.estado,
			'estado_pago': delivery.estado_pago,
			'recibido_por': recibido_por,
			'monto_pagado': str(delivery.monto_pagado),
			'is_customer_pickup': delivery.is_customer_pickup,
		},
	)
	return delivery


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


def list_pending_customer_notes(*, cliente):
	return list(
		NotaAjuste.objects.select_related('creada_por', 'aprobada_por')
		.filter(cliente=cliente, invoice__isnull=True, estado='APROBADA', monto_aplicado_cliente__gt=0)
		.order_by('aprobada_en', 'creada_en', 'id')
	)


def summarize_pending_customer_notes(*, cliente, notes=None):
	notes = list(notes) if notes is not None else list_pending_customer_notes(cliente=cliente)
	credit_total = Decimal('0.00')
	debit_total = Decimal('0.00')
	for nota in notes:
		remaining_amount = _clamp_non_negative_money(nota.monto_aplicado_cliente)
		nota.remaining_amount = remaining_amount
		if nota.tipo_documento == 'CREDITO':
			credit_total += remaining_amount
		else:
			debit_total += remaining_amount
	stored_credit = _clamp_non_negative_money(-min(Decimal(str(cliente.balance or '0.00')), Decimal('0.00')))
	available_credit_excluding_notes = _clamp_non_negative_money(stored_credit - credit_total)
	return {
		'notes': notes,
		'credit_total': _quantize_money(credit_total),
		'debit_total': _quantize_money(debit_total),
		'available_credit_excluding_notes': available_credit_excluding_notes,
	}


def _apply_selected_customer_notes_to_invoice(*, invoice, note_applications, usuario):
	if not note_applications:
		return

	notes = {
		nota.id: nota
		for nota in NotaAjuste.objects.select_for_update().filter(
			cliente=invoice.cliente,
			invoice__isnull=True,
			estado='APROBADA',
			id__in=list(note_applications.keys()),
		)
	}
	if len(notes) != len(note_applications):
		raise ValidationError(_('One or more selected customer notes are no longer available for this invoice.'))

	total_selected_credits = Decimal('0.00')
	total_selected_debits = Decimal('0.00')
	for note_id, requested_amount in note_applications.items():
		nota = notes[note_id]
		_ensure_quickbooks_not_locked(nota=nota)
		amount_to_apply = _clamp_non_negative_money(requested_amount)
		if nota.tipo_documento == 'CREDITO':
			total_selected_credits += amount_to_apply
		else:
			total_selected_debits += amount_to_apply
	max_credit_supported = _quantize_money(Decimal(str(invoice.subtotal or '0.00')) + total_selected_debits)
	if _quantize_money(total_selected_credits) > max_credit_supported:
		raise ValidationError(_('Selected credit notes exceed the invoice total available after the chosen debit notes.'))

	for note_id, requested_amount in note_applications.items():
		nota = notes[note_id]
		amount_to_apply = _clamp_non_negative_money(requested_amount)
		remaining_amount = _clamp_non_negative_money(nota.monto_aplicado_cliente)
		if amount_to_apply <= 0:
			continue
		if amount_to_apply > remaining_amount:
			raise ValidationError(_('The selected amount exceeds the remaining amount for note %(note)s.') % {'note': nota.numero})
		if nota.tipo_documento == 'CREDITO':
			invoice.total_creditos = _quantize_money(Decimal(str(invoice.total_creditos or '0.00')) + amount_to_apply)
			_apply_customer_balance_delta(cliente=invoice.cliente, delta=amount_to_apply)
		else:
			invoice.total_debitos = _quantize_money(Decimal(str(invoice.total_debitos or '0.00')) + amount_to_apply)
			_apply_customer_balance_delta(cliente=invoice.cliente, delta=-amount_to_apply)
		nota.monto_aplicado_invoice = _quantize_money(Decimal(str(nota.monto_aplicado_invoice or '0.00')) + amount_to_apply)
		nota.monto_aplicado_cliente = _quantize_money(remaining_amount - amount_to_apply)
		nota.save(update_fields=['monto_aplicado_invoice', 'monto_aplicado_cliente'])
		NotaAjusteAplicacion.objects.create(nota=nota, invoice=invoice, monto=amount_to_apply, aplicada_por=usuario)


@transaction.atomic
def generar_invoice_desde_picking(
	*,
	pedido,
	metodo_entrega,
	driver,
	usuario,
	suggested_unit_prices=None,
	line_discounts=None,
	applied_customer_credit=None,
	selected_note_applications=None,
	estimated_delivery_at=None,
):
	_validate_invoice_generation(pedido, metodo_entrega, driver)
	_ensure_picking_inventory_applied_for_invoice(pedido=pedido, usuario=usuario)
	suggested_unit_prices = suggested_unit_prices or {}
	line_discounts = line_discounts or {}

	invoice = Invoice.objects.create(
		pedido=pedido,
		cliente=pedido.cliente,
		metodo_entrega=metodo_entrega,
		driver=driver,
		despachador_notificado=True,
		notificado_en=timezone.now(),
		creada_por=usuario,
		fecha_documento=timezone.localdate(),
	)

	total = Decimal('0.00')
	invoice_items = []
	for item in pedido.items.select_related('presentacion__producto').all():
		quantity = int(item.cantidad or 0)
		list_unit_price = _quantize_money(item.precio)
		discount_amount_unit = Decimal('0.00')
		if item.descuento_aplicado and _quantize_money(item.descuento_monto) > 0:
			discount_amount_unit = _quantize_money(item.descuento_monto)
			final_unit_price = _clamp_non_negative_money(list_unit_price - discount_amount_unit)
			discount_percentage = Decimal('0.00')
			if list_unit_price > 0:
				discount_percentage = ((discount_amount_unit / list_unit_price) * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		else:
			discount_percentage = _parse_line_discount_percentage(line_discounts.get(item.id, '0'))
			final_unit_price = _calculate_discounted_unit_price(list_unit_price, discount_percentage)
			if discount_percentage > 0:
				discount_amount_unit = _quantize_money(list_unit_price - final_unit_price)
		line_total = _quantize_money(final_unit_price * Decimal(str(quantity)))
		suggested_unit_price = suggested_unit_prices.get(item.id)
		if suggested_unit_price in (None, ''):
			suggested_unit_price = resolve_presentacion_suggested_unit_price(
				presentacion=item.presentacion,
				base_case_price=final_unit_price,
			)
		else:
			suggested_unit_price = _quantize_money(suggested_unit_price)
		invoice_items.append(InvoiceItem(
			invoice=invoice,
			pedido_item=item,
			presentacion=item.presentacion,
			producto_nombre=item.presentacion.producto.nombre,
			presentacion_nombre=(item.presentacion.nombre_empaque_cliente or item.presentacion.nombre)[:120],
			cantidad_facturada=quantity,
			peso_por_caja=item.presentacion.peso_por_caja,
			precio_unitario_lista=list_unit_price if discount_percentage > 0 or discount_amount_unit > 0 else None,
			descuento_porcentaje=discount_percentage,
			descuento_monto_unitario=discount_amount_unit,
			precio_unitario=final_unit_price,
			precio_venta_sugerido_unitario=suggested_unit_price,
			subtotal=line_total,
		))
		total += line_total

	if not invoice_items:
		raise ValidationError(_('Add at least one verified item before generating the invoice.'))

	InvoiceItem.objects.bulk_create(invoice_items)
	invoice.subtotal = _quantize_money(total)
	invoice.save(update_fields=['subtotal', 'actualizada_en'])
	_apply_selected_customer_notes_to_invoice(invoice=invoice, note_applications=selected_note_applications or {}, usuario=usuario)
	cliente_credit_available = _clamp_non_negative_money(-min(Decimal(str(pedido.cliente.balance or '0.00')), Decimal('0.00')))
	applied_credit = _clamp_non_negative_money(applied_customer_credit or '0.00')
	if applied_credit > cliente_credit_available:
		raise ValidationError(_('The customer does not have enough available credit.'))
	remaining_total_after_notes = _remaining_invoice_balance_before_payment(invoice=invoice)
	if applied_credit > remaining_total_after_notes:
		raise ValidationError(_('The applied customer credit cannot exceed the remaining invoice total after selected notes.'))
	if applied_credit > 0:
		_apply_customer_balance_delta(cliente=pedido.cliente, delta=applied_credit)
	invoice.credito_cliente_aplicado = applied_credit
	totals = _calculate_invoice_totals(
		subtotal=total,
		credito_cliente_aplicado=applied_credit,
		total_creditos=invoice.total_creditos,
		total_debitos=invoice.total_debitos,
	)
	invoice.total_neto = totals['total_neto']
	invoice.saldo_cliente = totals['saldo_cliente']
	invoice.save(update_fields=['subtotal', 'credito_cliente_aplicado', 'total_creditos', 'total_debitos', 'total_neto', 'saldo_cliente', 'actualizada_en'])

	pedido.estado = 'INVOICE_GENERADA'
	pedido.save(update_fields=['estado', 'actualizada_en'])

	if metodo_entrega == 'RUTA_DRIVER':
		delivery = ensure_delivery_for_invoice(invoice)
		if delivery is not None and estimated_delivery_at is not None:
			delivery.estimated_delivery_at = estimated_delivery_at
			delivery.save(update_fields=['estimated_delivery_at', 'updated_at'])

	_create_invoice_notification(invoice)
	from config.auditoria.business_events import log_business_event
	log_business_event(
		usuario,
		action_label=_('Generated invoice %(invoice)s from sales order #%(pedido)s') % {
			'invoice': invoice.numero,
			'pedido': pedido.id,
		},
		action_category=AuditLog.CATEGORY_CREATE,
		entity_type='Invoice',
		entity_id=str(invoice.id),
		entity_label=invoice.numero,
		metadata={
			'pedido_id': pedido.id,
			'cliente': pedido.cliente.nombre_empresa,
			'metodo_entrega': metodo_entrega,
			'driver_id': getattr(driver, 'id', None),
			'driver_name': (driver.get_full_name() or driver.username) if driver else '',
			'total_neto': str(invoice.total_neto),
			'line_items': [
				{
					'presentacion_id': item.presentacion_id,
					'producto': item.producto_nombre,
					'presentacion': item.presentacion_nombre,
					'cantidad': item.cantidad_facturada,
					'precio_unitario': str(item.precio_unitario),
					'descuento_porcentaje': str(item.descuento_porcentaje),
					'subtotal': str(item.subtotal),
				}
				for item in invoice.items.all()
			],
		},
	)
	return invoice


@transaction.atomic
def generar_invoice_directa_backoffice(
	*,
	cliente,
	items_payload,
	metodo_entrega,
	usuario,
	nota_backoffice='',
	suggested_unit_prices=None,
	applied_customer_credit=None,
	selected_note_applications=None,
	driver=None,
	estimated_delivery_at=None,
):
	if metodo_entrega not in ALLOWED_DIRECT_INVOICE_DELIVERY_METHODS:
		raise ValidationError(_('Direct backoffice invoices only support customer pickup or route delivery with a driver.'))
	if metodo_entrega == 'RUTA_DRIVER' and driver is None:
		raise ValidationError(_('A driver is required for route deliveries.'))
	if metodo_entrega == 'CUSTOMER_PICK_UP' and driver is not None:
		raise ValidationError(_('Driver assignment is not allowed for customer pickup invoices.'))
	if not items_payload:
		raise ValidationError(_('Add at least one item before creating the direct invoice.'))

	pedido = crear_pedido_desde_items(
		cliente=cliente,
		items_payload=items_payload,
		origen='BACKOFFICE',
		vendedor=None,
		nota_cliente='',
		acepta_terminos=False,
		canal_toma='BACKOFFICE_DIRECT',
		bypass_stock_check=False,
		reservar_inventario=True,
	)
	item_ids = list(pedido.items.values_list('id', flat=True))
	aplicar_verificacion_picking_inventario(pedido=pedido, pedido_item_ids=item_ids, creado_por=usuario)
	pedido.estado = 'VERIFICADO_AJUSTADO'
	pedido.nota_backoffice = (nota_backoffice or '').strip()
	pedido.picking_verificado_en = timezone.now()
	pedido.save(update_fields=['estado', 'nota_backoffice', 'picking_verificado_en', 'actualizada_en'])

	line_discounts = {}
	for index, pedido_item in enumerate(pedido.items.order_by('id')):
		if index < len(items_payload):
			discount = items_payload[index].get('descuento_porcentaje', 0)
			if discount:
				line_discounts[pedido_item.id] = discount

	return generar_invoice_desde_picking(
		pedido=pedido,
		metodo_entrega=metodo_entrega,
		driver=driver,
		usuario=usuario,
		suggested_unit_prices=suggested_unit_prices,
		line_discounts=line_discounts,
		applied_customer_credit=applied_customer_credit,
		selected_note_applications=selected_note_applications,
		estimated_delivery_at=estimated_delivery_at,
	)


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
	from config.auditoria.business_events import log_business_event
	log_business_event(
		driver_user,
		action_label=_('Started delivery route for invoice %(invoice)s') % {'invoice': delivery.invoice.numero},
		action_category=AuditLog.CATEGORY_ACTION,
		entity_type='Delivery',
		entity_id=str(delivery.id),
		entity_label=delivery.invoice.numero,
		metadata={'invoice_id': delivery.invoice_id, 'estado': delivery.estado},
	)
	return delivery


def calculate_delivery_payment_delta(*, delivery, adjustment_note=None):
	payment_delta = _quantize_money(delivery.invoice.saldo_cliente)
	if adjustment_note is not None:
		adjustment_total = Decimal(str(adjustment_note.total or '0.00'))
		if adjustment_note.tipo_documento == 'CREDITO':
			payment_delta = _quantize_money(payment_delta - adjustment_total)
		else:
			payment_delta = _quantize_money(payment_delta + adjustment_total)
	return payment_delta


def calculate_delivery_collectible_balance(*, delivery, adjustment_note=None):
	return _clamp_non_negative_money(calculate_delivery_payment_delta(delivery=delivery, adjustment_note=adjustment_note))


def _empty_driver_payment_details():
	return {
		'entries': [],
		'metodo_pago': '',
		'monto_pagado': Decimal('0.00'),
		'monto_pagado_cash': Decimal('0.00'),
		'monto_pagado_cheque': Decimal('0.00'),
		'cheque_numero': '',
		'cheque_banco': '',
		'cheque_imagen': None,
		'transferencia_referencia': '',
		'tarjeta_ultimos_4': '',
		'tarjeta_autorizacion': '',
		'zelle_referencia': '',
		'zelle_remitente': '',
		'ach_referencia': '',
		'ach_cuenta_ultimos_4': '',
	}


def _build_driver_payment_entry(*, position, metodo_pago, monto, cheque_numero='', cheque_banco='', cheque_imagen=None,
	transferencia_referencia='', tarjeta_ultimos_4='', tarjeta_autorizacion='', zelle_referencia='', zelle_remitente='',
	ach_referencia='', ach_cuenta_ultimos_4=''):
	metodo_pago = (metodo_pago or '').strip()
	monto = _quantize_money(monto or '0')
	cheque_imagen = _normalize_uploaded_file(cheque_imagen)
	entry = {
		'position': position,
		'metodo_pago': metodo_pago,
		'monto': monto,
		'cheque_numero': (cheque_numero or '').strip(),
		'cheque_banco': (cheque_banco or '').strip(),
		'cheque_imagen': cheque_imagen,
		'transferencia_referencia': (transferencia_referencia or '').strip(),
		'tarjeta_ultimos_4': (tarjeta_ultimos_4 or '').strip(),
		'tarjeta_autorizacion': (tarjeta_autorizacion or '').strip(),
		'zelle_referencia': (zelle_referencia or '').strip(),
		'zelle_remitente': (zelle_remitente or '').strip(),
		'ach_referencia': (ach_referencia or '').strip(),
		'ach_cuenta_ultimos_4': (ach_cuenta_ultimos_4 or '').strip(),
	}
	if not metodo_pago:
		raise ValidationError(_('Select a payment method for each payment entry with an amount.'))
	if metodo_pago in {'MIXTO', 'MULTIPLE'}:
		raise ValidationError(_('Each payment entry must use a specific payment method.'))
	if monto <= 0:
		raise ValidationError(_('Each payment entry must include an amount greater than zero.'))
	if metodo_pago == 'CHEQUE':
		if not entry['cheque_numero'] or not entry['cheque_banco']:
			raise ValidationError(_('Cheque number and bank are required for cheque payments.'))
		if entry['cheque_imagen'] is None:
			raise ValidationError(_('A cheque image is required for cheque payments.'))
	elif metodo_pago == 'TRANSFERENCIA':
		if not entry['transferencia_referencia']:
			raise ValidationError(_('Transfer reference is required.'))
	elif metodo_pago == 'TARJETA':
		if len(entry['tarjeta_ultimos_4']) != 4 or not entry['tarjeta_autorizacion']:
			raise ValidationError(_('Card last four digits and authorization code are required.'))
	elif metodo_pago == 'ZELLE':
		if not entry['zelle_referencia'] or not entry['zelle_remitente']:
			raise ValidationError(_('Zelle reference and sender are required.'))
	elif metodo_pago == 'ACH':
		if not entry['ach_referencia'] or len(entry['ach_cuenta_ultimos_4']) != 4:
			raise ValidationError(_('ACH reference and account last four digits are required.'))
	return entry


def _resolve_driver_delivery_payment(*, payload, collectible_balance, payment_delta=None, payment_files=None, cheque_image_file=None):
	entries = []
	collectible_balance = _clamp_non_negative_money(collectible_balance)
	payment_delta = _quantize_money(payment_delta if payment_delta is not None else collectible_balance)
	if payment_files is None:
		payment_files = {}
	uses_new_rows = any(
		(payload.get(f'payment_method_{index}') or '').strip()
		or str(payload.get(f'payment_amount_{index}') or '').strip()
		or payment_files.get(f'payment_cheque_image_{index}')
		for index in range(1, 4)
	)
	legacy_has_payment_input = any(
		str(payload.get(field_name) or '').strip()
		for field_name in (
			'metodo_pago',
			'monto_pagado',
			'monto_pagado_cash',
			'monto_pagado_cheque',
			'cheque_numero',
			'cheque_banco',
			'transferencia_referencia',
			'tarjeta_ultimos_4',
			'tarjeta_autorizacion',
			'zelle_referencia',
			'zelle_remitente',
			'ach_referencia',
			'ach_cuenta_ultimos_4',
		)
	) or cheque_image_file is not None

	if collectible_balance <= 0 and not uses_new_rows and not legacy_has_payment_input:
		return _empty_driver_payment_details()

	if uses_new_rows:
		for index in range(1, 4):
			method = (payload.get(f'payment_method_{index}') or '').strip()
			amount = str(payload.get(f'payment_amount_{index}') or '').strip()
			row_has_input = any([
				method,
				amount,
				str(payload.get(f'payment_cheque_numero_{index}') or '').strip(),
				str(payload.get(f'payment_cheque_banco_{index}') or '').strip(),
				str(payload.get(f'payment_transferencia_referencia_{index}') or '').strip(),
				str(payload.get(f'payment_tarjeta_ultimos_4_{index}') or '').strip(),
				str(payload.get(f'payment_tarjeta_autorizacion_{index}') or '').strip(),
				str(payload.get(f'payment_zelle_referencia_{index}') or '').strip(),
				str(payload.get(f'payment_zelle_remitente_{index}') or '').strip(),
				str(payload.get(f'payment_ach_referencia_{index}') or '').strip(),
				str(payload.get(f'payment_ach_cuenta_ultimos_4_{index}') or '').strip(),
				payment_files.get(f'payment_cheque_image_{index}'),
			])
			if not row_has_input:
				continue
			entries.append(_build_driver_payment_entry(
				position=index,
				metodo_pago=method,
				monto=amount,
				cheque_numero=payload.get(f'payment_cheque_numero_{index}'),
				cheque_banco=payload.get(f'payment_cheque_banco_{index}'),
				cheque_imagen=_normalize_uploaded_file(payment_files.get(f'payment_cheque_image_{index}')),
				transferencia_referencia=payload.get(f'payment_transferencia_referencia_{index}'),
				tarjeta_ultimos_4=payload.get(f'payment_tarjeta_ultimos_4_{index}'),
				tarjeta_autorizacion=payload.get(f'payment_tarjeta_autorizacion_{index}'),
				zelle_referencia=payload.get(f'payment_zelle_referencia_{index}'),
				zelle_remitente=payload.get(f'payment_zelle_remitente_{index}'),
				ach_referencia=payload.get(f'payment_ach_referencia_{index}'),
				ach_cuenta_ultimos_4=payload.get(f'payment_ach_cuenta_ultimos_4_{index}'),
			))
	else:
		metodo_pago = (payload.get('metodo_pago') or '').strip()
		legacy_amount = _quantize_money(payload.get('monto_pagado') or '0')
		cash_amount = _quantize_money(payload.get('monto_pagado_cash') or '0')
		cheque_amount = _quantize_money(payload.get('monto_pagado_cheque') or '0')
		if not metodo_pago:
			raise ValidationError(_('A payment method is required when the delivery is paid.'))
		if metodo_pago == 'MIXTO':
			entries.append(_build_driver_payment_entry(
				position=1,
				metodo_pago='CASH',
				monto=cash_amount,
			))
			entries.append(_build_driver_payment_entry(
				position=2,
				metodo_pago='CHEQUE',
				monto=cheque_amount,
				cheque_numero=payload.get('cheque_numero'),
				cheque_banco=payload.get('cheque_banco'),
				cheque_imagen=_normalize_uploaded_file(cheque_image_file),
			))
		else:
			entries.append(_build_driver_payment_entry(
				position=1,
				metodo_pago=metodo_pago,
				monto=legacy_amount,
				cheque_numero=payload.get('cheque_numero'),
				cheque_banco=payload.get('cheque_banco'),
				cheque_imagen=_normalize_uploaded_file(cheque_image_file),
				transferencia_referencia=payload.get('transferencia_referencia'),
				tarjeta_ultimos_4=payload.get('tarjeta_ultimos_4'),
				tarjeta_autorizacion=payload.get('tarjeta_autorizacion'),
				zelle_referencia=payload.get('zelle_referencia'),
				zelle_remitente=payload.get('zelle_remitente'),
				ach_referencia=payload.get('ach_referencia'),
				ach_cuenta_ultimos_4=payload.get('ach_cuenta_ultimos_4'),
			))

	if not entries:
		if collectible_balance <= 0:
			return _empty_driver_payment_details()
		raise ValidationError(_('Add at least one payment method for paid deliveries.'))
	if len(entries) > 3:
		raise ValidationError(_('A maximum of three payment methods is allowed per delivery.'))

	total_paid = _quantize_money(sum(entry['monto'] for entry in entries))
	if collectible_balance <= 0:
		if payment_delta < 0:
			raise ValidationError(_('This delivery results in money due back to the customer. Do not record incoming payments.'))
		raise ValidationError(_('This delivery does not require collecting payment from the customer.'))
	if collectible_balance > 0 and total_paid <= 0:
		raise ValidationError(_('Paid deliveries must include a payment amount greater than zero.'))
	if total_paid > collectible_balance:
		raise ValidationError(_('The paid amount cannot exceed the customer balance.'))
	if total_paid < collectible_balance:
		raise ValidationError(_('The total paid amount must exactly match the amount due from the customer.'))

	cheque_entries = [entry for entry in entries if entry['metodo_pago'] == 'CHEQUE']
	single_entry = entries[0] if len(entries) == 1 else None
	return {
		'entries': entries,
		'metodo_pago': single_entry['metodo_pago'] if single_entry else 'MULTIPLE',
		'monto_pagado': total_paid,
		'monto_pagado_cash': _quantize_money(sum(entry['monto'] for entry in entries if entry['metodo_pago'] == 'CASH')),
		'monto_pagado_cheque': _quantize_money(sum(entry['monto'] for entry in cheque_entries)),
		'cheque_numero': cheque_entries[0]['cheque_numero'] if len(cheque_entries) == 1 else '',
		'cheque_banco': cheque_entries[0]['cheque_banco'] if len(cheque_entries) == 1 else '',
		'cheque_imagen': cheque_entries[0]['cheque_imagen'] if len(cheque_entries) == 1 else None,
		'transferencia_referencia': single_entry['transferencia_referencia'] if single_entry else '',
		'tarjeta_ultimos_4': single_entry['tarjeta_ultimos_4'] if single_entry else '',
		'tarjeta_autorizacion': single_entry['tarjeta_autorizacion'] if single_entry else '',
		'zelle_referencia': single_entry['zelle_referencia'] if single_entry else '',
		'zelle_remitente': single_entry['zelle_remitente'] if single_entry else '',
		'ach_referencia': single_entry['ach_referencia'] if single_entry else '',
		'ach_cuenta_ultimos_4': single_entry['ach_cuenta_ultimos_4'] if single_entry else '',
	}


@transaction.atomic
def complete_driver_delivery(*, delivery, driver_user, payload, evidence_files, adjustment_note=None, cheque_image_file=None, payment_files=None):
	if delivery.driver_id != driver_user.id:
		raise PermissionDenied(_('You are not assigned to this delivery.'))
	if delivery.is_completed:
		raise ValidationError(_('This delivery was already completed.'))
	evidence_files = _normalize_uploaded_files(evidence_files)

	estado_pago = (payload.get('estado_pago') or '').strip()
	recibido_por = (payload.get('recibido_por') or '').strip()
	motivo_no_pago = (payload.get('motivo_no_pago') or '').strip()
	signature_file = _build_signature_content_file(payload.get('firma_cliente_data'), delivery.id)
	payment_delta = calculate_delivery_payment_delta(delivery=delivery, adjustment_note=adjustment_note)
	collectible_balance = calculate_delivery_collectible_balance(delivery=delivery, adjustment_note=adjustment_note)
	payment_details = None

	if estado_pago not in {'PAGADO', 'NO_PAGADO'}:
		raise ValidationError(_('Select a valid payment status.'))
	if not recibido_por:
		raise ValidationError(_('Recipient name is required for delivered orders.'))
	if estado_pago == 'PAGADO':
		payment_details = _resolve_driver_delivery_payment(
			payload=payload,
			collectible_balance=collectible_balance,
			payment_delta=payment_delta,
			payment_files=payment_files,
			cheque_image_file=cheque_image_file,
		)
	else:
		if not motivo_no_pago:
			raise ValidationError(_('A reason is required when the customer does not pay.'))
		if not evidence_files:
			raise ValidationError(_('Upload at least one evidence photo when the customer does not pay.'))

	if delivery.estado != 'EN_RUTA':
		start_delivery_route(delivery=delivery, driver_user=driver_user)

	return _finalize_delivery_completion(
		delivery=delivery,
		acting_user=driver_user,
		payload=payload,
		evidence_files=evidence_files,
		estado_pago=estado_pago,
		recibido_por=recibido_por,
		motivo_no_pago=motivo_no_pago,
		signature_file=signature_file,
		payment_details=payment_details,
	)


@transaction.atomic
def complete_customer_pickup_from_backoffice(*, invoice, backoffice_user, payload, evidence_files, adjustment_note=None, cheque_image_file=None, payment_files=None):
	delivery = ensure_customer_pickup_delivery_for_invoice(invoice)
	if delivery is None:
		raise ValidationError(_('This invoice is not configured for customer pick up.'))
	if delivery.is_completed:
		raise ValidationError(_('This customer pick up was already completed.'))
	evidence_files = _normalize_uploaded_files(evidence_files)

	estado_pago = (payload.get('estado_pago') or '').strip()
	recibido_por = (payload.get('recibido_por') or '').strip()
	motivo_no_pago = (payload.get('motivo_no_pago') or '').strip()
	signature_file = _build_signature_content_file(payload.get('firma_cliente_data'), delivery.id)
	payment_delta = calculate_delivery_payment_delta(delivery=delivery, adjustment_note=adjustment_note)
	collectible_balance = calculate_delivery_collectible_balance(delivery=delivery, adjustment_note=adjustment_note)
	payment_details = None

	if estado_pago not in {'PAGADO', 'NO_PAGADO'}:
		raise ValidationError(_('Select a valid payment status.'))
	if not recibido_por:
		raise ValidationError(_('Recipient name is required for delivered orders.'))
	if estado_pago == 'PAGADO':
		payment_details = _resolve_driver_delivery_payment(
			payload=payload,
			collectible_balance=collectible_balance,
			payment_delta=payment_delta,
			payment_files=payment_files,
			cheque_image_file=cheque_image_file,
		)
	else:
		if not motivo_no_pago:
			raise ValidationError(_('A reason is required when the customer does not pay.'))
		if not evidence_files:
			raise ValidationError(_('Upload at least one evidence photo when the customer does not pay.'))

	return _finalize_delivery_completion(
		delivery=delivery,
		acting_user=backoffice_user,
		payload=payload,
		evidence_files=evidence_files,
		estado_pago=estado_pago,
		recibido_por=recibido_por,
		motivo_no_pago=motivo_no_pago,
		signature_file=signature_file,
		payment_details=payment_details,
		completed_by=backoffice_user,
	)


def _extract_note_presentacion(invoice_item):
	if invoice_item is None:
		return None
	if invoice_item.presentacion_id:
		return invoice_item.presentacion
	if invoice_item.pedido_item_id:
		return invoice_item.pedido_item.presentacion
	return None


def _normalize_content_term(value):
	normalized = unicodedata.normalize('NFKD', str(value or '').strip().lower())
	return ''.join(char for char in normalized if not unicodedata.combining(char))


def _content_term_aliases(value):
	normalized = _normalize_content_term(value)
	aliases = {normalized}
	if normalized.endswith('s'):
		aliases.add(normalized[:-1])
	if normalized.endswith('es'):
		aliases.add(normalized[:-2])
	return {alias for alias in aliases if alias}


def _resolve_partial_return_target(presentacion):
	units_per_package = max(int(getattr(presentacion, 'unidades', 0) or 0), 1)
	if units_per_package == 1:
		return {'presentacion': presentacion, 'contenido_fraccionado': ''}

	content_aliases = _content_term_aliases(presentacion.tipo_contenido)
	child_presentacion = None
	for candidate in Presentacion.objects.select_related('producto').filter(producto_id=presentacion.producto_id).exclude(id=presentacion.id).order_by('unidades', 'id'):
		candidate_aliases = set()
		candidate_aliases.update(_content_term_aliases(candidate.nombre))
		candidate_aliases.update(_content_term_aliases(candidate.nombre_en))
		candidate_aliases.update(_content_term_aliases(candidate.tipo_contenido))
		candidate_aliases.update(_content_term_aliases(candidate.tipo_contenido_en))
		if content_aliases & candidate_aliases:
			child_presentacion = candidate
			break

	if child_presentacion is not None:
		return {'presentacion': child_presentacion, 'contenido_fraccionado': ''}

	return {
		'presentacion': presentacion,
		'contenido_fraccionado': (presentacion.tipo_contenido or '').strip(),
	}


def _apply_credit_note_inventory(*, nota, movement_type, delta_fisico, created_by=None):
	presentacion_ids = [item.presentacion_id for item in nota.items.all() if item.presentacion_id]
	fractional_pairs = [
		(item.presentacion.producto_id, item.contenido_fraccionado)
		for item in nota.items.select_related('presentacion__producto').all()
		if item.presentacion_id and item.contenido_fraccionado
	]
	if not presentacion_ids and not fractional_pairs:
		return
	stock_map = _lock_stock_records(presentacion_ids)
	fractional_stock_map = _lock_fractional_stock_records(fractional_pairs)
	for note_item in nota.items.select_related('presentacion__producto').all():
		if not note_item.presentacion_id:
			continue
		if note_item.contenido_fraccionado:
			fractional_stock = fractional_stock_map[(note_item.presentacion.producto_id, note_item.contenido_fraccionado)]
			_apply_fractional_inventory_change(
				stock=fractional_stock,
				delta_fisico=delta_fisico * note_item.cantidad,
				observacion=nota.descripcion,
				referencia=nota.numero,
				invoice=nota.invoice,
				nota_ajuste=nota,
				nota_ajuste_item=note_item,
				creado_por=created_by,
			)
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
	total_pagado = Decimal('0.00')
	if hasattr(invoice, 'delivery') and invoice.delivery.estado_pago == 'PAGADO':
		total_pagado = invoice.delivery.monto_pagado
	totals = _calculate_invoice_totals(
		subtotal=invoice.subtotal,
		credito_cliente_aplicado=invoice.credito_cliente_aplicado,
		total_creditos=invoice.total_creditos,
		total_debitos=invoice.total_debitos,
		total_pagado=total_pagado,
	)
	invoice.total_neto = totals['total_neto']
	invoice.saldo_cliente = totals['saldo_cliente']
	invoice.save(update_fields=['credito_cliente_aplicado', 'total_creditos', 'total_debitos', 'total_neto', 'saldo_cliente', 'actualizada_en'])
	return invoice


@transaction.atomic
def crear_nota_ajuste(
	*,
	cliente,
	invoice,
	tipo_ajuste='PRODUCTO',
	tipo_documento,
	motivo,
	tipo_credito,
	descripcion,
	usuario,
	items_payload,
	monto=None,
):
	if cliente is None:
		raise ValidationError(_('Select a customer to save the adjustment.'))
	if invoice is not None and invoice.cliente_id != cliente.id:
		raise ValidationError(_('The selected invoice does not belong to the selected customer.'))
	if invoice is not None:
		_ensure_quickbooks_not_locked(invoice=invoice)
	tipo_ajuste = (tipo_ajuste or 'PRODUCTO').strip().upper()
	if tipo_ajuste not in {'PRODUCTO', 'FINANCIERO'}:
		raise ValidationError(_('Select a valid adjustment type.'))

	tipo_documento = (tipo_documento or '').strip()
	motivo = (motivo or '').strip()
	tipo_credito = (tipo_credito or '').strip()
	descripcion = (descripcion or '').strip()
	if not tipo_documento:
		raise ValidationError(_('Select a note type to save the adjustment.'))
	if not motivo:
		raise ValidationError(_('Select a reason to save the adjustment.'))
	if tipo_documento == 'CREDITO' and tipo_ajuste == 'PRODUCTO' and tipo_credito not in {'CREDIT_RETURN', 'CREDIT_DUMP'}:
		raise ValidationError(_('Product credit notes must use Credit Return or Credit Dump.'))
	if tipo_documento == 'CREDITO' and tipo_ajuste == 'FINANCIERO' and tipo_credito != 'CREDIT_DUMP':
		raise ValidationError(_('Financial credit notes must use Credit Dump.'))
	general_amount = _quantize_money(monto or '0.00')
	returned_units_map = build_invoice_item_returned_units_map(invoice) if invoice is not None else {}

	normalized_items = []
	for payload in items_payload or []:
		invoice_item = payload.get('invoice_item')
		if invoice_item is not None and invoice is not None and getattr(invoice_item, 'invoice_id', None) != invoice.id:
			raise ValidationError(_('Each adjustment item must belong to the selected invoice.'))
		presentacion = payload.get('presentacion') or _extract_note_presentacion(invoice_item)
		descripcion_item = (payload.get('descripcion') or '').strip()
		quantity = int(payload.get('cantidad') or 0)
		loose_units = int(payload.get('cantidad_unidades') or 0)
		line_amount = _quantize_money(payload.get('monto_unitario') or '0')
		has_quantity = quantity > 0
		has_loose_units = loose_units > 0
		has_amount = line_amount > 0
		if (has_quantity or has_loose_units) and not has_amount:
			raise ValidationError(_('Enter an amount greater than zero for each selected adjustment item.'))
		if has_amount and not (has_quantity or has_loose_units):
			raise ValidationError(_('Enter a quantity greater than zero for each selected adjustment item.'))
		if not has_quantity and not has_loose_units and not has_amount:
			continue
		if tipo_ajuste == 'PRODUCTO' and presentacion is None:
			raise ValidationError(_('Select a product presentation for each manual product adjustment line.'))

		units_per_package = max(int(getattr(presentacion, 'unidades', 0) or 0), 1)
		if loose_units and units_per_package == 1:
			quantity += loose_units
			loose_units = 0
		elif loose_units:
			additional_packages, loose_units = divmod(loose_units, units_per_package)
			quantity += additional_packages

		requested_units = (quantity * units_per_package) + loose_units
		if requested_units <= 0:
			continue

		if invoice_item is not None:
			max_units = resolve_invoice_item_returnable_units(invoice_item, returned_units_map)
			if requested_units > max_units:
				raise ValidationError(
					_('The returned quantity for %(product)s cannot exceed the invoiced units.') % {
						'product': invoice_item.producto_nombre,
					}
				)

		package_total = Decimal('0.00')
		package_unit_amount = Decimal('0.00')
		loose_total = Decimal('0.00')
		loose_unit_amount = Decimal('0.00')
		if quantity > 0 and loose_units > 0:
			loose_total = _quantize_money(line_amount * Decimal(str(loose_units)) / Decimal(str(requested_units)))
			package_total = _quantize_money(line_amount - loose_total)
			package_unit_amount = _quantize_money(package_total / Decimal(str(quantity)))
			loose_unit_amount = _quantize_money(loose_total / Decimal(str(loose_units)))
		elif quantity > 0:
			package_total = line_amount
			package_unit_amount = _quantize_money(line_amount / Decimal(str(quantity)))
		else:
			loose_total = line_amount
			loose_unit_amount = _quantize_money(line_amount / Decimal(str(loose_units)))

		if quantity > 0:
			normalized_items.append({
				'invoice_item': invoice_item,
				'presentacion': presentacion,
				'contenido_fraccionado': '',
				'descripcion': descripcion_item,
				'cantidad': quantity,
				'monto_unitario': package_unit_amount,
				'line_total': package_total,
			})

		if loose_units > 0:
			partial_target = _resolve_partial_return_target(presentacion)
			normalized_items.append({
				'invoice_item': invoice_item,
				'presentacion': partial_target['presentacion'],
				'contenido_fraccionado': partial_target['contenido_fraccionado'],
				'descripcion': descripcion_item,
				'cantidad': loose_units,
				'monto_unitario': loose_unit_amount,
				'line_total': loose_total,
			})

	if tipo_ajuste == 'PRODUCTO' and not normalized_items:
		raise ValidationError(_('Add at least one item before saving the adjustment.'))
	if tipo_ajuste == 'FINANCIERO' and general_amount <= 0:
		raise ValidationError(_('Enter an amount greater than zero for financial adjustment notes.'))
	if tipo_ajuste == 'FINANCIERO':
		normalized_items = []

	inventario_estado = 'PENDIENTE' if tipo_ajuste == 'PRODUCTO' and tipo_documento == 'CREDITO' and tipo_credito == 'CREDIT_RETURN' else 'NO_APLICA'
	nota = NotaAjuste(
		cliente=cliente,
		invoice=invoice,
		tipo_ajuste=tipo_ajuste,
		tipo_documento=tipo_documento,
		motivo=motivo,
		tipo_credito=tipo_credito,
		descripcion=descripcion,
		monto=general_amount,
		inventario_estado=inventario_estado,
		creada_por=usuario,
	)
	nota.full_clean()
	nota.save()

	total = general_amount
	for payload in normalized_items:
		invoice_item = payload.get('invoice_item')
		presentacion = payload.get('presentacion') or _extract_note_presentacion(invoice_item)
		cantidad = int(payload.get('cantidad') or 0)
		monto_unitario = payload.get('monto_unitario')
		line_total = _quantize_money(payload.get('line_total') or (monto_unitario * Decimal(str(cantidad))))
		NotaAjusteItem.objects.create(
			nota=nota,
			invoice_item=invoice_item,
			presentacion=presentacion,
			contenido_fraccionado=payload.get('contenido_fraccionado') or '',
			descripcion=payload.get('descripcion') or getattr(invoice_item, 'producto_nombre', getattr(getattr(presentacion, 'producto', None), 'nombre', nota.descripcion or (invoice.numero if invoice else cliente.nombre_empresa))),
			cantidad=cantidad,
			monto_unitario=monto_unitario,
			total=line_total,
		)
		total += line_total

	nota.monto = _quantize_money(total)
	nota.total = nota.monto
	nota.impacto_saldo = nota.total
	nota.save(update_fields=['monto', 'total', 'impacto_saldo'])

	crear_notificacion_backoffice(
		titulo=_('Adjustment note %(number)s requires review') % {'number': nota.numero},
		mensaje=_('Adjustment note %(number)s is ready for BackOffice to approve or reject.') % {'number': nota.numero},
		tipo='NOTA_AJUSTE',
		url=f'/facturacion/backoffice/invoices/{invoice.id}/' if invoice else '/facturacion/backoffice/notes/create/',
	)
	return nota


@transaction.atomic
def crear_nota_ajuste_desde_invoice(
	*,
	invoice,
	tipo_ajuste='PRODUCTO',
	tipo_documento,
	motivo,
	tipo_credito,
	descripcion,
	usuario,
	items_payload,
	monto=None,
):
	return crear_nota_ajuste(
		cliente=invoice.cliente,
		invoice=invoice,
		tipo_ajuste=tipo_ajuste,
		tipo_documento=tipo_documento,
		motivo=motivo,
		tipo_credito=tipo_credito,
		descripcion=descripcion,
		usuario=usuario,
		items_payload=items_payload,
		monto=monto,
	)


def _apply_note_to_customer_balance(*, nota, multiplier):
	if not nota.monto_aplicado_cliente:
		return
	amount = _quantize_money(Decimal(str(nota.monto_aplicado_cliente)) * Decimal(str(multiplier)))
	if nota.tipo_documento == 'CREDITO':
		_apply_customer_balance_delta(cliente=nota.cliente, delta=-amount)
	else:
		_apply_customer_balance_delta(cliente=nota.cliente, delta=amount)


def _apply_note_to_invoice(*, nota, multiplier):
	if not nota.invoice_id or not nota.monto_aplicado_invoice:
		return
		
	invoice = nota.invoice
	amount = _quantize_money(Decimal(str(nota.monto_aplicado_invoice)) * Decimal(str(multiplier)))
	if nota.tipo_documento == 'CREDITO':
		invoice.total_creditos = _quantize_money(invoice.total_creditos + amount)
	else:
		invoice.total_debitos = _quantize_money(invoice.total_debitos + amount)
	_recalculate_invoice_balances(invoice)


def _allocate_credit_note_amount(*, nota):
	invoice_amount = Decimal('0.00')
	client_amount = Decimal('0.00')
	if nota.invoice_id:
		invoice = nota.invoice
		pending_balance = _clamp_non_negative_money(invoice.saldo_cliente)
		invoice_amount = nota.total
		client_amount = _clamp_non_negative_money(nota.total - pending_balance)
	else:
		client_amount = nota.total
	nota.monto_aplicado_invoice = invoice_amount
	nota.monto_aplicado_cliente = client_amount


def _allocate_debit_note_amount(*, nota):
	invoice_amount = nota.total if nota.invoice_id else Decimal('0.00')
	client_amount = Decimal('0.00') if nota.invoice_id else nota.total
	nota.monto_aplicado_invoice = _quantize_money(invoice_amount)
	nota.monto_aplicado_cliente = _quantize_money(client_amount)


@transaction.atomic
def aprobar_nota_ajuste(*, nota, usuario):
	_ensure_quickbooks_not_locked(nota=nota, invoice=nota.invoice if nota.invoice_id else None)
	if nota.estado != 'BORRADOR':
		raise ValidationError(_('Only draft adjustment notes can be approved.'))

	if nota.tipo_documento == 'CREDITO':
		_allocate_credit_note_amount(nota=nota)
		_apply_note_to_invoice(nota=nota, multiplier=1)
		_apply_note_to_customer_balance(nota=nota, multiplier=1)
		if nota.tipo_ajuste == 'PRODUCTO' and nota.tipo_credito == 'CREDIT_RETURN':
			_apply_credit_note_inventory(nota=nota, movement_type='ENTRADA_NOTA_CREDITO', delta_fisico=1, created_by=usuario)
			nota.inventario_estado = 'PROCESADO'
	else:
		_allocate_debit_note_amount(nota=nota)
		_apply_note_to_invoice(nota=nota, multiplier=1)
		_apply_note_to_customer_balance(nota=nota, multiplier=1)
		if nota.inventario_estado == 'PENDIENTE':
			nota.inventario_estado = 'NO_APLICA'

	nota.estado = 'APROBADA'
	nota.aprobada_por = usuario
	nota.aprobada_en = timezone.now()
	nota.save(update_fields=['estado', 'aprobada_por', 'aprobada_en', 'inventario_estado', 'monto_aplicado_invoice', 'monto_aplicado_cliente'])
	return nota


@transaction.atomic
def anular_nota_ajuste(*, nota, usuario=None, motivo='', crear_registro=True):
	_ensure_quickbooks_not_locked(nota=nota, invoice=nota.invoice if nota.invoice_id else None)
	if nota.estado == 'ANULADA':
		return nota

	if nota.estado == 'APROBADA':
		_revert_nota_ajuste_effects(nota=nota, usuario=usuario)

	nota.estado = 'ANULADA'
	nota.anulada_en = timezone.now()
	nota.anulada_por = usuario
	nota.motivo_anulacion = (motivo or '').strip()
	if nota.inventario_estado == 'PROCESADO':
		nota.inventario_estado = 'ANULADO'
	nota.save(update_fields=['estado', 'anulada_en', 'anulada_por', 'motivo_anulacion', 'inventario_estado'])

	if crear_registro:
		_create_void_registro(
			tipo_documento='NOTA_CREDITO' if nota.tipo_documento == 'CREDITO' else 'NOTA_DEBITO',
			numero_documento=nota.numero,
			documento_id=nota.id,
			cliente=nota.cliente,
			invoice=nota.invoice,
			nota=nota,
			motivo=nota.motivo_anulacion,
			usuario=usuario,
			snapshot=_build_nota_void_snapshot(nota),
		)
	return nota


def _nota_allows_quickbooks_bypass_on_delete(nota):
	if nota.estado not in {'BORRADOR', 'APROBADA', 'ANULADA'}:
		return False
	if nota.invoice_id and nota.is_invoice_locked():
		return False
	return True


@transaction.atomic
def eliminar_nota_ajuste(*, nota, force_quickbooks=False):
	purge_quickbooks_lock = force_quickbooks or _nota_allows_quickbooks_bypass_on_delete(nota)
	if not purge_quickbooks_lock:
		_ensure_quickbooks_not_locked(nota=nota, invoice=nota.invoice if nota.invoice_id else None)
	if nota.estado == 'APROBADA':
		_revert_nota_ajuste_effects(nota=nota, usuario=nota.anulada_por or nota.aprobada_por)
	FacturacionRegistroAnulacion.objects.filter(nota=nota).delete()
	nota.delete()


def _revert_nota_ajuste_effects(*, nota, usuario=None):
	if nota.tipo_documento == 'CREDITO':
		_apply_note_to_invoice(nota=nota, multiplier=-1)
		_apply_note_to_customer_balance(nota=nota, multiplier=-1)
		if nota.inventario_estado == 'PROCESADO':
			_apply_credit_note_inventory(nota=nota, movement_type='REVERSO_NOTA_CREDITO', delta_fisico=-1, created_by=usuario)
	else:
		_apply_note_to_invoice(nota=nota, multiplier=-1)
		_apply_note_to_customer_balance(nota=nota, multiplier=-1)


def _build_nota_void_snapshot(nota):
	return {
		'numero': nota.numero,
		'tipo_documento': nota.tipo_documento,
		'tipo_ajuste': nota.tipo_ajuste,
		'tipo_credito': nota.tipo_credito,
		'total': str(nota.total),
		'impacto_saldo': str(nota.impacto_saldo),
		'inventario_estado': nota.inventario_estado,
		'items': [
			{
				'descripcion': item.descripcion,
				'cantidad': item.cantidad,
				'total': str(item.total),
				'presentacion_id': item.presentacion_id,
			}
			for item in nota.items.all()
		],
	}


def _build_invoice_void_snapshot(invoice):
	return {
		'numero': invoice.numero,
		'subtotal': str(invoice.subtotal),
		'total_neto': str(invoice.total_neto),
		'saldo_cliente': str(invoice.saldo_cliente),
		'metodo_entrega': invoice.metodo_entrega,
		'items': [
			{
				'producto_nombre': item.producto_nombre,
				'presentacion_nombre': item.presentacion_nombre,
				'cantidad_facturada': item.cantidad_facturada,
				'subtotal': str(item.subtotal),
				'presentacion_id': item.presentacion_id,
			}
			for item in invoice.items.all()
		],
	}


def _create_void_registro(*, tipo_documento, numero_documento, documento_id, cliente, invoice=None, nota=None, motivo='', usuario=None, snapshot=None):
	return FacturacionRegistroAnulacion.objects.create(
		tipo_documento=tipo_documento,
		numero_documento=numero_documento,
		documento_id=documento_id,
		cliente=cliente,
		invoice=invoice,
		nota=nota,
		motivo=(motivo or '').strip(),
		snapshot=snapshot or {},
		anulado_por=usuario,
	)


INVOICE_DELETE_CONFIRMATION_PHRASES = frozenset({'confirm', 'delete'})


def invoice_delete_requires_confirmation_phrase(invoice):
	from config.integrations.quickbooks.sync import is_sync_locked

	return invoice.estado != 'ANULADA' and is_sync_locked(invoice)


def validate_invoice_delete_confirmation_phrase(*, invoice, confirmation_phrase):
	if not invoice_delete_requires_confirmation_phrase(invoice):
		return
	normalized = str(confirmation_phrase or '').strip().lower()
	if normalized not in INVOICE_DELETE_CONFIRMATION_PHRASES:
		raise ValidationError(
			_('Type CONFIRM or DELETE to permanently remove invoice %(invoice)s that is already synced with QuickBooks.') % {
				'invoice': invoice.numero,
			}
		)


def _validate_invoice_void_delete_allowed(invoice):
	if invoice.estado == 'ANULADA':
		raise ValidationError(_('This invoice is already voided.'))
	delivery = getattr(invoice, 'delivery', None)
	if delivery and delivery.estado in {'EN_RUTA', 'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}:
		raise ValidationError(_('Cannot void or delete an invoice that is already on route or delivered.'))


def _invoice_allows_quickbooks_bypass_on_delete(invoice):
	if invoice.estado == 'ANULADA':
		return True
	return invoice.delivery_blocks_void_delete()


def _validate_invoice_delete_allowed(invoice):
	if invoice.estado == 'ANULADA':
		return
	if invoice.delivery_blocks_void_delete():
		return
	_validate_invoice_void_delete_allowed(invoice)


def _reverse_invoice_customer_note_applications(*, invoice):
	for application in NotaAjusteAplicacion.objects.select_for_update().filter(invoice=invoice).select_related('nota'):
		nota = application.nota
		amount = _quantize_money(application.monto)
		if amount <= 0:
			continue
		if nota.tipo_documento == 'CREDITO':
			invoice.total_creditos = _quantize_money(Decimal(str(invoice.total_creditos or '0.00')) - amount)
			_apply_customer_balance_delta(cliente=nota.cliente, delta=-amount)
		else:
			invoice.total_debitos = _quantize_money(Decimal(str(invoice.total_debitos or '0.00')) - amount)
			_apply_customer_balance_delta(cliente=nota.cliente, delta=amount)
		nota.monto_aplicado_invoice = _quantize_money(Decimal(str(nota.monto_aplicado_invoice or '0.00')) - amount)
		nota.monto_aplicado_cliente = _quantize_money(Decimal(str(nota.monto_aplicado_cliente or '0.00')) + amount)
		nota.save(update_fields=['monto_aplicado_invoice', 'monto_aplicado_cliente'])
		application.delete()
	_recalculate_invoice_balances(invoice)


def _reverse_invoice_customer_credit(*, invoice):
	applied_credit = _quantize_money(invoice.credito_cliente_aplicado)
	if applied_credit > 0:
		_apply_customer_balance_delta(cliente=invoice.cliente, delta=-applied_credit)
		invoice.credito_cliente_aplicado = Decimal('0.00')
		_recalculate_invoice_balances(invoice)


def _void_linked_invoice_notes(*, invoice, usuario, motivo=''):
	for nota in NotaAjuste.objects.select_for_update().filter(invoice=invoice, estado='APROBADA').order_by('id'):
		child_motivo = motivo or _('Voided automatically because invoice %(invoice)s was voided.') % {'invoice': invoice.numero}
		anular_nota_ajuste(nota=nota, usuario=usuario, motivo=child_motivo, crear_registro=True)


@transaction.atomic
def anular_invoice(*, invoice, usuario, motivo=''):
	_ensure_quickbooks_not_locked(invoice=invoice)
	_validate_invoice_void_delete_allowed(invoice)
	motivo = (motivo or '').strip()

	_void_linked_invoice_notes(invoice=invoice, usuario=usuario, motivo=motivo)
	_reverse_invoice_customer_note_applications(invoice=invoice)
	_reverse_invoice_customer_credit(invoice=invoice)
	restaurar_inventario_por_anulacion_factura(pedido=invoice.pedido, invoice=invoice, creado_por=usuario)

	pedido = invoice.pedido
	pedido.estado = 'VERIFICADO_AJUSTADO'
	pedido.save(update_fields=['estado', 'actualizada_en'])

	invoice.estado = 'ANULADA'
	invoice.anulada_en = timezone.now()
	invoice.anulada_por = usuario
	invoice.motivo_anulacion = motivo
	invoice.despachador_notificado = False
	invoice.notificado_en = None
	invoice.save(update_fields=[
		'estado',
		'anulada_en',
		'anulada_por',
		'motivo_anulacion',
		'despachador_notificado',
		'notificado_en',
		'actualizada_en',
	])

	_create_void_registro(
		tipo_documento='INVOICE',
		numero_documento=invoice.numero,
		documento_id=invoice.id,
		cliente=invoice.cliente,
		invoice=invoice,
		motivo=motivo,
		usuario=usuario,
		snapshot=_build_invoice_void_snapshot(invoice),
	)
	from config.auditoria.business_events import log_business_event
	from config.auditoria.models import AuditLog
	log_business_event(
		usuario,
		action_label=_('Voided invoice'),
		action_category=AuditLog.CATEGORY_DELETE,
		entity_type='Invoice',
		entity_id=invoice.id,
		entity_label=invoice.numero,
		metadata={'motivo': motivo, 'pedido_id': pedido.id},
	)
	return invoice


@transaction.atomic
def eliminar_invoice(*, invoice, force_quickbooks=False):
	purge_quickbooks_lock = force_quickbooks or _invoice_allows_quickbooks_bypass_on_delete(invoice)
	if not purge_quickbooks_lock:
		_ensure_quickbooks_not_locked(invoice=invoice)
	if invoice.estado != 'ANULADA':
		_validate_invoice_delete_allowed(invoice)

	for nota in list(NotaAjuste.objects.filter(invoice=invoice).order_by('id')):
		eliminar_nota_ajuste(nota=nota, force_quickbooks=purge_quickbooks_lock)

	if invoice.estado != 'ANULADA':
		_reverse_invoice_customer_note_applications(invoice=invoice)
		_reverse_invoice_customer_credit(invoice=invoice)
		restaurar_inventario_por_anulacion_factura(pedido=invoice.pedido, invoice=invoice, creado_por=invoice.anulada_por or invoice.creada_por)

	pedido = invoice.pedido
	pedido_id = pedido.id
	FacturacionRegistroAnulacion.objects.filter(invoice=invoice).delete()
	invoice.delete()
	pedido.refresh_from_db()
	pedido.estado = 'CANCELADO'
	pedido.save(update_fields=['estado', 'actualizada_en'])
	return pedido_id


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


@transaction.atomic
def mark_delivery_unpaid_from_backoffice(*, delivery, backoffice_user, motivo_no_pago):
	if not delivery.is_completed:
		raise ValidationError(_('Only completed deliveries can be corrected from BackOffice.'))
	if delivery.estado_pago != 'PAGADO':
		raise ValidationError(_('Only paid deliveries can be marked as unpaid from BackOffice.'))

	reason = (motivo_no_pago or '').strip()
	if not reason:
		raise ValidationError(_('A reason is required when marking the delivery as unpaid.'))

	delivery.estado_pago = 'NO_PAGADO'
	delivery.estado = 'ENTREGADA_SIN_PAGO'
	delivery.metodo_pago = ''
	delivery.monto_pagado = Decimal('0.00')
	delivery.monto_pagado_cash = Decimal('0.00')
	delivery.monto_pagado_cheque = Decimal('0.00')
	delivery.motivo_no_pago = reason
	delivery.transferencia_referencia = ''
	delivery.tarjeta_ultimos_4 = ''
	delivery.tarjeta_autorizacion = ''
	delivery.zelle_referencia = ''
	delivery.zelle_remitente = ''
	delivery.ach_referencia = ''
	delivery.ach_cuenta_ultimos_4 = ''
	delivery.cheque_numero = ''
	delivery.cheque_banco = ''
	delivery.cheque_imagen = None
	delivery.client_blocked_on_delivery = True
	delivery.client_unlocked_by = None
	delivery.client_unlocked_at = None
	delivery.payments.all().delete()
	delivery.save(update_fields=[
		'estado',
		'estado_pago',
		'metodo_pago',
		'monto_pagado',
		'monto_pagado_cash',
		'monto_pagado_cheque',
		'motivo_no_pago',
		'transferencia_referencia',
		'tarjeta_ultimos_4',
		'tarjeta_autorizacion',
		'zelle_referencia',
		'zelle_remitente',
		'ach_referencia',
		'ach_cuenta_ultimos_4',
		'cheque_numero',
		'cheque_banco',
		'cheque_imagen',
		'client_blocked_on_delivery',
		'client_unlocked_by',
		'client_unlocked_at',
		'updated_at',
	])

	cliente = delivery.invoice.cliente
	cliente.credit_hold = True
	cliente.save(update_fields=['credit_hold'])
	_recalculate_invoice_balances(delivery.invoice)
	from config.auditoria.business_events import log_business_event
	log_business_event(
		backoffice_user,
		action_label=_('Marked delivery as unpaid for invoice %(invoice)s') % {'invoice': delivery.invoice.numero},
		action_category=AuditLog.CATEGORY_UPDATE,
		entity_type='Delivery',
		entity_id=str(delivery.id),
		entity_label=delivery.invoice.numero,
		metadata={'motivo_no_pago': reason},
	)
	return delivery


def get_recent_customer_invoice_items_by_presentation(*, cliente, presentation_ids, limit=2):
	presentation_ids = [presentation_id for presentation_id in presentation_ids if presentation_id]
	if not cliente or not presentation_ids:
		return {}

	recent_items = (
		InvoiceItem.objects
		.select_related('invoice', 'invoice__pedido')
		.filter(
			invoice__cliente=cliente,
			invoice__estado='GENERADA',
			presentacion_id__in=presentation_ids,
		)
		.order_by('presentacion_id', '-invoice__fecha_documento', '-invoice__creada_en', '-invoice_id', '-id')
	)

	history = {presentation_id: [] for presentation_id in presentation_ids}
	history_counts = {presentation_id: 0 for presentation_id in presentation_ids}
	for invoice_item in recent_items:
		presentacion_id = invoice_item.presentacion_id
		if presentacion_id not in history:
			continue
		if history_counts[presentacion_id] >= limit:
			continue
		invoice_item.sale_reference_date = resolve_invoice_sale_reference_date(invoice_item.invoice)
		history[presentacion_id].append(invoice_item)
		history_counts[presentacion_id] += 1

	return history


def resolve_invoice_sale_reference_date(invoice):
	if getattr(invoice, 'fecha_documento', None):
		return invoice.fecha_documento
	if getattr(invoice, 'creada_en', None):
		return timezone.localtime(invoice.creada_en).date()
	return timezone.localdate()


def build_customer_invoice_sale_price_options(*, cliente, presentacion, limit=2):
	history = get_recent_customer_invoice_items_by_presentation(
		cliente=cliente,
		presentation_ids=[presentacion.id],
		limit=limit,
	).get(presentacion.id, [])

	options = []
	for index, invoice_item in enumerate(history, start=1):
		price_value = _quantize_money(invoice_item.precio_unitario)
		sale_date = getattr(invoice_item, 'sale_reference_date', None) or resolve_invoice_sale_reference_date(invoice_item.invoice)
		date_label = sale_date.strftime('%m/%d/%Y')
		options.append({
			'key': f'invoice_sale_{index}',
			'value': format(price_value, '.2f'),
			'label': _('%(date)s - $%(price)s') % {
				'date': date_label,
				'price': format(price_value, '.2f'),
			},
			'sale_date': sale_date,
			'invoice_item_id': invoice_item.id,
		})
	return options