from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.pedidos.models import Pedido
from config.usuarios.models import Usuario
from config.usuarios.permissions import internal_permission_required

from .models import Delivery, DeliveryEvidencePhoto, Invoice, NotaAjuste
from .services import (
	aprobar_nota_ajuste,
	anular_nota_ajuste,
	build_google_maps_route_url,
	complete_driver_delivery,
	crear_nota_ajuste_desde_invoice,
	ensure_delivery_for_invoice,
	generar_invoice_desde_picking,
	resolve_presentacion_suggested_unit_price,
	start_delivery_route,
	unlock_client_from_delivery,
)


def _format_pdf_money(value):
	amount = Decimal(str(value or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
	return f'${amount:,.2f}'


def _resolve_invoice_barcode(item):
	presentacion = item.presentacion
	if not presentacion or not presentacion.producto_id:
		return ''
	return (presentacion.producto.codigo_barras or '').strip()


def _resolve_invoice_pack_size(item):
	presentacion = item.presentacion
	if not presentacion:
		return item.presentacion_nombre
	return f'{item.presentacion_nombre} x {presentacion.unidades}'


def _resolve_invoice_suggested_case_price(item):
	saved_suggested_unit_price = getattr(item, 'precio_venta_sugerido_unitario', None)
	if saved_suggested_unit_price is not None:
		presentacion = item.presentacion
		if presentacion and presentacion.unidades:
			return (Decimal(str(saved_suggested_unit_price)) * Decimal(str(presentacion.unidades))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		return Decimal(str(saved_suggested_unit_price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

	base_price = Decimal(str(item.precio_unitario or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
	presentacion = item.presentacion
	if not presentacion:
		return base_price

	configured_prices = sorted({
		Decimal(str(price or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		for price in (
			presentacion.precio_1,
			presentacion.precio_2,
			presentacion.precio_3,
			presentacion.precio_4,
			presentacion.precio_5,
		)
		if Decimal(str(price or '0')) > 0
	})
	for configured_price in configured_prices:
		if configured_price > base_price:
			return configured_price
	return configured_prices[-1] if configured_prices else base_price


def _resolve_invoice_suggested_unit_price(item):
	saved_suggested_unit_price = getattr(item, 'precio_venta_sugerido_unitario', None)
	if saved_suggested_unit_price is not None:
		return Decimal(str(saved_suggested_unit_price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

	suggested_case_price = _resolve_invoice_suggested_case_price(item)
	presentacion = item.presentacion
	if not presentacion or not presentacion.unidades:
		return suggested_case_price
	return (suggested_case_price / Decimal(str(presentacion.unidades))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _parse_invoice_suggested_unit_price(value):
	text = str(value or '').strip().replace(',', '.')
	if not text:
		return None
	try:
		parsed = Decimal(text)
	except (InvalidOperation, TypeError, ValueError):
		raise ValidationError(_('Suggested retail per unit must be a valid number.'))
	if parsed <= 0:
		raise ValidationError(_('Suggested retail per unit must be greater than zero.'))
	return parsed.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _extract_invoice_suggested_unit_prices(pedido, post_data):
	suggested_prices = {}
	for item in pedido.items.all():
		suggested_price = _parse_invoice_suggested_unit_price(post_data.get(f'suggested_unit_price_{item.id}'))
		if suggested_price is not None:
			suggested_prices[item.id] = suggested_price
	return suggested_prices


def _build_invoice_pdf_item_data(invoice):
	items = []
	for item in invoice.items.select_related('presentacion__producto').all():
		barcode = _resolve_invoice_barcode(item)
		items.append({
			'barcode': barcode,
			'product_name': item.producto_nombre,
			'pack_size': _resolve_invoice_pack_size(item),
			'quantity': str(item.cantidad_facturada),
			'customer_price': _format_pdf_money(item.precio_unitario),
			'suggested_unit_price': _format_pdf_money(_resolve_invoice_suggested_unit_price(item)),
			'subtotal': _format_pdf_money(item.subtotal),
		})
	return items


def _invoice_pdf_response(invoice):
	buffer = BytesIO()
	document = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
	styles = getSampleStyleSheet()
	meta_label_style = ParagraphStyle('InvoiceMetaLabel', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=9, textColor=colors.HexColor('#475569'), leading=11)
	meta_value_style = ParagraphStyle('InvoiceMetaValue', parent=styles['BodyText'], fontSize=10, leading=12)
	section_title_style = ParagraphStyle('InvoiceSectionTitle', parent=styles['Heading4'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor('#0f172a'), spaceAfter=4)
	note_style = ParagraphStyle('InvoiceNote', parent=styles['BodyText'], fontSize=8, textColor=colors.HexColor('#64748b'), leading=10)

	sales_rep = '-'
	if invoice.pedido.vendedor_id:
		sales_rep = invoice.pedido.vendedor.get_full_name() or invoice.pedido.vendedor.username
	driver_name = (invoice.driver.get_full_name() or invoice.driver.username) if invoice.driver else '-'
	ship_to = invoice.delivery.route_address if hasattr(invoice, 'delivery') else ', '.join(filter(None, [
		invoice.cliente.direccion,
		invoice.cliente.ciudad,
		invoice.cliente.estado,
		invoice.cliente.codigo_postal,
		invoice.cliente.pais,
	]))
	item_rows = _build_invoice_pdf_item_data(invoice)

	content = []

	header_table = Table([
		[
			Paragraph(f'<font size="20"><b>Invoice</b></font><br/><font size="12">{invoice.numero}</font>', styles['BodyText']),
			Table([
				[Paragraph(_('Customer no.'), meta_label_style), Paragraph(str(invoice.cliente_id), meta_value_style), Paragraph(_('Date'), meta_label_style), Paragraph(timezone.localtime(invoice.creada_en).strftime('%m/%d/%Y'), meta_value_style)],
				[Paragraph(_('Order no.'), meta_label_style), Paragraph(str(invoice.pedido_id), meta_value_style), Paragraph(_('Generated on'), meta_label_style), Paragraph(timezone.localtime(invoice.creada_en).strftime('%m/%d/%Y %H:%M'), meta_value_style)],
				[Paragraph(_('Sales rep'), meta_label_style), Paragraph(sales_rep, meta_value_style), Paragraph(_('Driver'), meta_label_style), Paragraph(driver_name, meta_value_style)],
			], colWidths=[72, 120, 78, 120]),
		],
	], colWidths=[220, 470])
	header_table.setStyle(TableStyle([
		('VALIGN', (0, 0), (-1, -1), 'TOP'),
		('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#0b3d91')),
		('TEXTCOLOR', (0, 0), (0, 0), colors.white),
		('LEFTPADDING', (0, 0), (0, 0), 14),
		('RIGHTPADDING', (0, 0), (0, 0), 14),
		('TOPPADDING', (0, 0), (0, 0), 12),
		('BOTTOMPADDING', (0, 0), (0, 0), 12),
		('BOX', (1, 0), (1, 0), 0.5, colors.HexColor('#cbd5e1')),
		('LEFTPADDING', (1, 0), (1, 0), 10),
		('RIGHTPADDING', (1, 0), (1, 0), 10),
		('TOPPADDING', (1, 0), (1, 0), 8),
		('BOTTOMPADDING', (1, 0), (1, 0), 8),
	]))
	content.extend([header_table, Spacer(1, 12)])

	party_table = Table([
		[
			Paragraph(
				f'<b>{_("Sold to")}</b><br/>{invoice.cliente.nombre_empresa}<br/>{invoice.cliente.direccion}<br/>{invoice.cliente.ciudad}, {invoice.cliente.estado} {invoice.cliente.codigo_postal or ""}<br/>{invoice.cliente.pais}',
				styles['BodyText'],
			),
			Paragraph(
				f'<b>{_("Ship to")}</b><br/>{invoice.cliente.nombre_empresa}<br/>{ship_to}',
				styles['BodyText'],
			),
			Paragraph(
				f'<b>{_("Terms")}</b><br/>{_("Customer balance")}: {_format_pdf_money(invoice.saldo_cliente)}<br/>{_("Delivery method")}: {invoice.get_metodo_entrega_display()}',
				styles['BodyText'],
			),
		],
	], colWidths=[235, 235, 220])
	party_table.setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
		('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
		('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
		('VALIGN', (0, 0), (-1, -1), 'TOP'),
		('LEFTPADDING', (0, 0), (-1, -1), 10),
		('RIGHTPADDING', (0, 0), (-1, -1), 10),
		('TOPPADDING', (0, 0), (-1, -1), 8),
		('BOTTOMPADDING', (0, 0), (-1, -1), 8),
	]))
	content.extend([party_table, Spacer(1, 12)])

	content.append(Paragraph(_('Line items with barcode, customer case price and a suggested resale value per unit.'), note_style))
	content.append(Spacer(1, 8))

	rows = [[_('Barcode'), _('Product'), _('Pack / size'), _('Qty'), _('Customer price'), _('Suggested retail / unit'), _('Subtotal')]]
	for item in item_rows:
		barcode_cell = Paragraph('-', styles['BodyText'])
		if item['barcode']:
			barcode_cell = code128.Code128(item['barcode'], barHeight=22, barWidth=0.6, humanReadable=True)
		rows.append([
			barcode_cell,
			Paragraph(item['product_name'], styles['BodyText']),
			Paragraph(item['pack_size'], styles['BodyText']),
			item['quantity'],
			item['customer_price'],
			item['suggested_unit_price'],
			item['subtotal'],
		])

	table = Table(rows, colWidths=[130, 200, 95, 42, 82, 94, 78], repeatRows=1)
	table.setStyle(TableStyle([
		('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0b3d91')),
		('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
		('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
		('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
		('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
		('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
		('ALIGN', (3, 1), (-1, -1), 'CENTER'),
		('BOTTOMPADDING', (0, 0), (-1, -1), 8),
		('TOPPADDING', (0, 0), (-1, -1), 8),
	]))
	content.append(table)
	content.append(Spacer(1, 16))

	totals_table = Table([
		[Paragraph(_('Subtotal'), meta_label_style), Paragraph(_format_pdf_money(invoice.subtotal), meta_value_style)],
		[Paragraph(_('Credits'), meta_label_style), Paragraph(_format_pdf_money(invoice.total_creditos), meta_value_style)],
		[Paragraph(_('Debits'), meta_label_style), Paragraph(_format_pdf_money(invoice.total_debitos), meta_value_style)],
		[Paragraph(_('Customer balance'), section_title_style), Paragraph(f'<b>{_format_pdf_money(invoice.saldo_cliente)}</b>', styles['BodyText'])],
	], colWidths=[110, 120])
	totals_table.setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
		('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
		('BACKGROUND', (0, 0), (-1, -2), colors.HexColor('#f8fafc')),
		('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#dbeafe')),
		('LEFTPADDING', (0, 0), (-1, -1), 10),
		('RIGHTPADDING', (0, 0), (-1, -1), 10),
		('TOPPADDING', (0, 0), (-1, -1), 7),
		('BOTTOMPADDING', (0, 0), (-1, -1), 7),
	]))
	content.extend([
		Paragraph(_('Pricing note'), section_title_style),
		Paragraph(_('Suggested retail per unit uses the next configured presentation price tier when available. It is a reference for resale, not a mandatory selling price.'), note_style),
		Spacer(1, 8),
		totals_table,
	])

	document.build(content)
	pdf = buffer.getvalue()
	buffer.close()

	Invoice.objects.filter(id=invoice.id).update(pdf_generado_en=timezone.now())

	response = HttpResponse(pdf, content_type='application/pdf')
	response['Content-Disposition'] = f'attachment; filename="invoice-{invoice.numero}.pdf"'
	return response


def _parse_non_negative_quantity(value):
	text = str(value or '').strip()
	if not text:
		return 0
	try:
		quantity = int(text)
	except (TypeError, ValueError):
		raise ValidationError(_('Quantities must be whole numbers.'))
	if quantity < 0:
		raise ValidationError(_('Quantities cannot be negative.'))
	return quantity


def _parse_tracking_decimal(value, *, label, decimal_places, min_value=None, max_value=None, required=False):
	text = str(value or '').strip()
	if not text:
		if required:
			raise ValidationError(_('%(field)s is required.') % {'field': label})
		return None
	try:
		parsed = Decimal(text)
	except (InvalidOperation, TypeError, ValueError):
		raise ValidationError(_('%(field)s must be a valid number.') % {'field': label})
	if min_value is not None and parsed < Decimal(str(min_value)):
		raise ValidationError(_('%(field)s is below the allowed range.') % {'field': label})
	if max_value is not None and parsed > Decimal(str(max_value)):
		raise ValidationError(_('%(field)s is above the allowed range.') % {'field': label})
	return parsed.quantize(Decimal(decimal_places))


def _delivery_tracking_payload(delivery):
	location_updated = delivery.location_updated_at
	location_age_seconds = None
	if location_updated:
		location_age_seconds = max(int((timezone.now() - location_updated).total_seconds()), 0)
	return {
		'delivery_id': delivery.id,
		'invoice_id': delivery.invoice_id,
		'invoice_number': delivery.invoice.numero,
		'customer_name': delivery.invoice.cliente.nombre_empresa,
		'driver_name': delivery.driver.get_full_name() or delivery.driver.username,
		'status': delivery.get_estado_display(),
		'payment_status': delivery.get_estado_pago_display(),
		'has_location': delivery.has_live_location,
		'latitude': float(delivery.current_latitude) if delivery.current_latitude is not None else None,
		'longitude': float(delivery.current_longitude) if delivery.current_longitude is not None else None,
		'accuracy_meters': float(delivery.current_accuracy_meters) if delivery.current_accuracy_meters is not None else None,
		'speed_mps': float(delivery.current_speed_mps) if delivery.current_speed_mps is not None else None,
		'heading': float(delivery.current_heading) if delivery.current_heading is not None else None,
		'location_updated_at': location_updated.isoformat() if location_updated else None,
		'location_updated_label': timezone.localtime(location_updated).strftime('%d/%m/%Y %H:%M:%S') if location_updated else '-',
		'location_age_seconds': location_age_seconds,
		'route_started_label': timezone.localtime(delivery.route_started_at).strftime('%d/%m/%Y %H:%M') if delivery.route_started_at else '-',
		'maps_url': delivery.live_google_maps_url,
		'destination_maps_url': delivery.google_maps_url,
		'destination_address': delivery.route_address,
		'invoice_detail_url': reverse('backoffice_invoice_detail', args=[delivery.invoice_id]),
		'invoice_tracking_url': reverse('backoffice_invoice_live_tracking', args=[delivery.invoice_id]),
	}


def _live_driver_deliveries_queryset():
	return Delivery.objects.select_related('invoice__cliente__usuario', 'driver').filter(estado='EN_RUTA').order_by('-location_updated_at', '-route_started_at', 'created_at')


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoices_list(request):
	base_queryset = Invoice.objects.select_related('pedido__cliente', 'driver', 'creada_por', 'delivery').prefetch_related('items', 'notas_ajuste').order_by('-creada_en')
	view_mode = request.GET.get('view', 'pending')

	pending_queryset = base_queryset.filter(
		estado='GENERADA',
		despachador_notificado=False,
	).exclude(
		delivery__estado__in=['ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'],
	)
	ready_queryset = base_queryset.filter(
		estado='GENERADA',
		despachador_notificado=True,
	).exclude(
		delivery__estado__in=['ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'],
	)
	delivered_queryset = base_queryset.filter(
		estado='GENERADA',
		delivery__estado__in=['ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'],
	)
	cancelled_queryset = base_queryset.filter(estado='ANULADA')

	querysets = {
		'pending': pending_queryset,
		'ready': ready_queryset,
		'delivered': delivered_queryset,
		'cancelled': cancelled_queryset,
	}
	if view_mode not in querysets:
		view_mode = 'pending'

	invoices = querysets[view_mode]

	return render(request, 'backoffice/invoices_list.html', {
		'invoices': invoices,
		'view_mode': view_mode,
		'pending_count': pending_queryset.count(),
		'ready_count': ready_queryset.count(),
		'delivered_count': delivered_queryset.count(),
		'cancelled_count': cancelled_queryset.count(),
	})


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_generate_invoice(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	metodo_entrega = request.POST.get('metodo_entrega') or ''
	driver = None
	driver_id = request.POST.get('driver_id') or ''
	if driver_id:
		driver = get_object_or_404(Usuario, id=driver_id, role='driver', is_active=True)

	try:
		suggested_unit_prices = _extract_invoice_suggested_unit_prices(pedido, request.POST)
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega=metodo_entrega,
			driver=driver,
			usuario=request.user,
			suggested_unit_prices=suggested_unit_prices,
		)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	messages.success(request, _('Invoice generated successfully.'))
	return redirect('backoffice_invoice_detail', invoice_id=invoice.id)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoice_detail(request, invoice_id):
	invoice = get_object_or_404(
		Invoice.objects.select_related('pedido__cliente__usuario', 'driver', 'creada_por').prefetch_related('items__presentacion__producto', 'notas_ajuste__items', 'delivery__evidence_photos', 'delivery__notification_logs'),
		id=invoice_id,
	)
	if invoice.metodo_entrega == 'RUTA_DRIVER' and invoice.driver_id:
		ensure_delivery_for_invoice(invoice)
		invoice.refresh_from_db()
	return render(request, 'backoffice/invoice_detail.html', {
		'invoice': invoice,
	})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoice_live_tracking(request, invoice_id):
	invoice = get_object_or_404(
		Invoice.objects.select_related('pedido__cliente__usuario', 'driver', 'delivery__driver'),
		id=invoice_id,
	)
	if not hasattr(invoice, 'delivery'):
		messages.error(request, _('This invoice does not have a driver delivery assigned.'))
		return redirect('backoffice_invoice_detail', invoice_id=invoice.id)
	return render(request, 'backoffice/invoice_live_tracking.html', {
		'invoice': invoice,
		'delivery': invoice.delivery,
		'tracking_payload': _delivery_tracking_payload(invoice.delivery),
	})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoice_tracking_data(request, invoice_id):
	invoice = get_object_or_404(
		Invoice.objects.select_related('delivery__driver'),
		id=invoice_id,
	)
	if not hasattr(invoice, 'delivery'):
		return JsonResponse({'success': False, 'message': str(_('This invoice does not have a driver delivery assigned.'))}, status=404)
	return JsonResponse({'success': True, 'tracking': _delivery_tracking_payload(invoice.delivery)})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_live_drivers(request):
	deliveries = list(_live_driver_deliveries_queryset())
	return render(request, 'backoffice/live_drivers.html', {
		'deliveries': deliveries,
		'tracking_payloads': [_delivery_tracking_payload(delivery) for delivery in deliveries],
	})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_live_drivers_data(request):
	deliveries = list(_live_driver_deliveries_queryset())
	return JsonResponse({
		'success': True,
		'drivers': [_delivery_tracking_payload(delivery) for delivery in deliveries],
	})


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_invoice_create_note(request, invoice_id):
	invoice = get_object_or_404(Invoice.objects.prefetch_related('items__presentacion__producto'), id=invoice_id)
	if request.method != 'POST':
		return redirect('backoffice_invoice_detail', invoice_id=invoice.id)

	try:
		items_payload = [
			{
				'invoice_item': item,
				'cantidad': _parse_non_negative_quantity(request.POST.get(f'note_qty_{item.id}')),
				'monto_unitario': request.POST.get(f'note_amount_{item.id}'),
			}
			for item in invoice.items.all()
		]
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento=request.POST.get('tipo_documento') or '',
			motivo=request.POST.get('motivo') or '',
			tipo_credito=request.POST.get('tipo_credito') or '',
			descripcion=request.POST.get('descripcion') or '',
			usuario=request.user,
			items_payload=items_payload,
		)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note %(note)s saved as draft.') % {'note': nota.numero})

	return redirect('backoffice_invoice_detail', invoice_id=invoice.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_invoice_approve_note(request, note_id):
	nota = get_object_or_404(NotaAjuste.objects.select_related('invoice'), id=note_id)
	if request.method != 'POST':
		return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)

	try:
		aprobar_nota_ajuste(nota=nota, usuario=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note approved successfully.'))
	return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_invoice_cancel_note(request, note_id):
	nota = get_object_or_404(NotaAjuste.objects.select_related('invoice'), id=note_id)
	if request.method != 'POST':
		return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)

	try:
		anular_nota_ajuste(nota=nota)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note cancelled successfully.'))
	return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoice_pdf(request, invoice_id):
	invoice = get_object_or_404(Invoice.objects.select_related('pedido__cliente', 'driver').prefetch_related('items', 'notas_ajuste'), id=invoice_id)
	return _invoice_pdf_response(invoice)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_unlock_delivery_client(request, delivery_id):
	delivery = get_object_or_404(Delivery.objects.select_related('invoice__cliente'), id=delivery_id)
	if request.method != 'POST':
		return redirect('backoffice_invoice_detail', invoice_id=delivery.invoice_id)
	unlock_client_from_delivery(delivery=delivery, backoffice_user=request.user)
	messages.success(request, _('Customer unlocked successfully.'))
	return redirect('backoffice_invoice_detail', invoice_id=delivery.invoice_id)


@login_required
@internal_permission_required('driver.delivery.view')
def driver_delivery_list(request):
	view_mode = request.GET.get('view')
	completed_statuses = {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}
	base_queryset = Delivery.objects.select_related('invoice__cliente__usuario', 'driver').prefetch_related('invoice__items').filter(driver=request.user)
	if view_mode == 'completed':
		deliveries = base_queryset.filter(estado__in=completed_statuses).order_by('-delivered_at', '-updated_at', '-created_at')
	else:
		view_mode = 'active'
		deliveries = base_queryset.exclude(estado__in=completed_statuses).order_by('estado', 'created_at')
	return render(request, 'backoffice/driver_delivery_list.html', {
		'deliveries': deliveries,
		'view_mode': view_mode,
		'active_count': base_queryset.exclude(estado__in=completed_statuses).count(),
		'completed_count': base_queryset.filter(estado__in=completed_statuses).count(),
	})


@login_required
@internal_permission_required('driver.delivery.view')
def driver_delivery_detail(request, delivery_id):
	delivery = get_object_or_404(
		Delivery.objects.select_related('invoice__cliente__usuario', 'driver').prefetch_related('invoice__items', 'evidence_photos', 'notification_logs'),
		id=delivery_id,
		driver=request.user,
	)
	return render(request, 'backoffice/driver_delivery_detail.html', {'delivery': delivery, 'invoice': delivery.invoice})


@login_required
@internal_permission_required('driver.delivery.manage')
def driver_delivery_upload_evidence(request, delivery_id):
	delivery = get_object_or_404(
		Delivery.objects.select_related('invoice__cliente__usuario', 'driver'),
		id=delivery_id,
		driver=request.user,
	)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)

	evidence_files = request.FILES.getlist('evidence_photos')
	if not evidence_files:
		messages.error(request, _('Select at least one evidence photo to upload.'))
		return redirect('driver_delivery_detail', delivery_id=delivery.id)

	for uploaded_file in evidence_files:
		DeliveryEvidencePhoto.objects.create(delivery=delivery, image=uploaded_file)

	messages.success(request, _('Evidence photos uploaded successfully.'))
	return redirect('driver_delivery_detail', delivery_id=delivery.id)


@login_required
@internal_permission_required('driver.delivery.manage')
def driver_delivery_tracking(request, delivery_id):
	delivery = get_object_or_404(
		Delivery.objects.select_related('invoice__cliente__usuario', 'driver'),
		id=delivery_id,
		driver=request.user,
	)
	return render(request, 'backoffice/driver_delivery_tracking.html', {
		'delivery': delivery,
		'invoice': delivery.invoice,
		'tracking_payload': _delivery_tracking_payload(delivery),
	})


@login_required
@internal_permission_required('driver.delivery.manage')
def driver_delivery_update_location(request, delivery_id):
	if request.method != 'POST':
		return JsonResponse({'success': False, 'message': str(_('Only POST requests are allowed.'))}, status=405)

	delivery = get_object_or_404(Delivery.objects.select_related('invoice', 'driver'), id=delivery_id, driver=request.user)
	if delivery.is_completed:
		return JsonResponse({'success': False, 'message': str(_('Completed deliveries no longer accept live tracking updates.'))}, status=400)

	try:
		latitude = _parse_tracking_decimal(request.POST.get('latitude'), label=_('Latitude'), decimal_places='0.000001', min_value=-90, max_value=90, required=True)
		longitude = _parse_tracking_decimal(request.POST.get('longitude'), label=_('Longitude'), decimal_places='0.000001', min_value=-180, max_value=180, required=True)
		accuracy_meters = _parse_tracking_decimal(request.POST.get('accuracy_meters'), label=_('Accuracy'), decimal_places='0.01', min_value=0)
		speed_mps = _parse_tracking_decimal(request.POST.get('speed_mps'), label=_('Speed'), decimal_places='0.01', min_value=0)
		heading = _parse_tracking_decimal(request.POST.get('heading'), label=_('Heading'), decimal_places='0.01', min_value=0, max_value=360)
	except ValidationError as exc:
		return JsonResponse({'success': False, 'message': exc.messages[0] if getattr(exc, 'messages', None) else str(exc)}, status=400)

	delivery.current_latitude = latitude
	delivery.current_longitude = longitude
	delivery.current_accuracy_meters = accuracy_meters
	delivery.current_speed_mps = speed_mps
	delivery.current_heading = heading
	delivery.location_updated_at = timezone.now()
	delivery.save(update_fields=[
		'current_latitude',
		'current_longitude',
		'current_accuracy_meters',
		'current_speed_mps',
		'current_heading',
		'location_updated_at',
		'updated_at',
	])
	return JsonResponse({'success': True, 'tracking': _delivery_tracking_payload(delivery)})


@login_required
@internal_permission_required('driver.delivery.manage')
def driver_delivery_start_route(request, delivery_id):
	delivery = get_object_or_404(Delivery.objects.select_related('invoice__cliente'), id=delivery_id, driver=request.user)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	try:
		start_delivery_route(delivery=delivery, driver_user=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	return redirect('driver_delivery_tracking', delivery_id=delivery.id)


@login_required
@internal_permission_required('driver.delivery.view')
def driver_delivery_route(request):
	selected_delivery_ids = request.GET.getlist('delivery_ids')
	if not selected_delivery_ids:
		messages.error(request, _('Select at least one assigned invoice to generate the route.'))
		return redirect('driver_delivery_list')

	deliveries = Delivery.objects.filter(
		driver=request.user,
		estado__in={'ASIGNADA', 'EN_RUTA'},
		id__in=selected_delivery_ids,
	).order_by('created_at')
	if not deliveries.exists():
		messages.error(request, _('The selected invoices are no longer available for route generation.'))
		return redirect('driver_delivery_list')

	try:
		maps_url = build_google_maps_route_url(deliveries)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('driver_delivery_list')
	return redirect(maps_url)


@login_required
@internal_permission_required('driver.delivery.manage')
def driver_delivery_complete(request, delivery_id):
	delivery = get_object_or_404(Delivery.objects.select_related('invoice__cliente__usuario'), id=delivery_id, driver=request.user)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	try:
		complete_driver_delivery(
			delivery=delivery,
			driver_user=request.user,
			payload=request.POST,
			evidence_files=request.FILES.getlist('evidence_photos'),
		)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Delivery saved successfully.'))
	return redirect('driver_delivery_detail', delivery_id=delivery.id)


@login_required
@internal_permission_required('driver.delivery.view')
def driver_invoice_pdf(request, delivery_id):
	delivery = get_object_or_404(Delivery.objects.select_related('invoice__cliente', 'invoice__driver'), id=delivery_id, driver=request.user)
	invoice = Invoice.objects.select_related('pedido__cliente', 'driver').prefetch_related('items', 'notas_ajuste').get(id=delivery.invoice_id)
	return _invoice_pdf_response(invoice)
