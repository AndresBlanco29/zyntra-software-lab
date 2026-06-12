from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.clientes.models import Cliente
from config.core.workflow_badges import build_delivery_workflow_badge
from config.productos.models import Presentacion
from config.core.pdf_branding import (
	BRAND_BORDER,
	BRAND_MUTED_TEXT,
	BRAND_PRIMARY,
	BRAND_SOFT_BLUE,
	BRAND_SURFACE,
	BRAND_TEXT,
	build_pdf_logo_image,
)
from config.integrations.quickbooks.sync import is_sync_locked
from config.notificaciones.models import Notificacion
from config.pedidos.models import Pedido
from config.usuarios.models import Usuario
from config.usuarios.permissions import internal_permission_required

from .models import Delivery, DeliveryEvidencePhoto, Invoice, NotaAjuste, NotaAjusteEvidencePhoto
from .services import (
	DEFAULT_SUGGESTED_PROFIT_PERCENTAGE,
	_normalize_uploaded_file,
	_normalize_uploaded_files,
	aprobar_nota_ajuste,
	anular_nota_ajuste,
	build_google_maps_route_url,
	calculate_delivery_collectible_balance,
	complete_driver_delivery,
	crear_nota_ajuste,
	crear_nota_ajuste_desde_invoice,
	ensure_delivery_for_invoice,
	generar_invoice_directa_backoffice,
	generar_invoice_desde_picking,
	list_pending_customer_notes,
	resolve_presentacion_suggested_unit_price,
	summarize_pending_customer_notes,
	start_delivery_route,
	unlock_client_from_delivery,
)


def _format_pdf_money(value):
	amount = Decimal(str(value or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
	return f'${amount:,.2f}'


def _validate_invoice_is_not_quickbooks_locked(invoice):
	if is_sync_locked(invoice):
		raise ValidationError(_('Invoice %(invoice)s is locked because it is already synced with QuickBooks.') % {'invoice': invoice.numero})


def _validate_note_is_not_quickbooks_locked(nota):
	if is_sync_locked(nota):
		raise ValidationError(_('Adjustment note %(note)s is locked because it is already synced with QuickBooks.') % {'note': nota.numero})
	if nota.invoice_id:
		_validate_invoice_is_not_quickbooks_locked(nota.invoice)


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


def _is_invoice_suggested_price_default(item, suggested_unit_price):
	try:
		default_unit_price = resolve_presentacion_suggested_unit_price(
			presentacion=item.presentacion,
			base_case_price=item.precio_unitario,
		)
	except Exception:
		return False
	return Decimal(str(suggested_unit_price or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) == default_unit_price


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


def _parse_adjustment_amount(value):
	text = str(value or '').strip().replace(',', '.')
	if not text:
		return Decimal('0.00')
	try:
		parsed = Decimal(text)
	except (InvalidOperation, TypeError, ValueError):
		raise ValidationError(_('Amounts must be valid numbers.'))
	if parsed < 0:
		raise ValidationError(_('Amounts cannot be negative.'))
	return parsed.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _parse_customer_credit_to_apply(cliente, post_data, *, available_credit=None):
	use_credit = str(post_data.get('use_customer_credit') or '').strip().lower() in {'1', 'true', 'on', 'yes'}
	if not use_credit:
		return Decimal('0.00')
	requested_credit = _parse_adjustment_amount(post_data.get('customer_credit_to_apply'))
	if available_credit is None:
		available_credit = getattr(cliente, 'available_credit', Decimal('0.00'))
	if requested_credit > available_credit:
		raise ValidationError(_('The requested customer credit exceeds the customer available balance.'))
	return requested_credit


def _parse_general_note_applications(cliente, post_data):
	note_applications = {}
	for nota in list_pending_customer_notes(cliente=cliente):
		requested_amount = _parse_adjustment_amount(post_data.get(f'general_note_apply_{nota.id}'))
		remaining_amount = Decimal(str(nota.monto_aplicado_cliente or '0.00')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		if requested_amount > remaining_amount:
			raise ValidationError(_('The selected amount exceeds the remaining amount for note %(note)s.') % {'note': nota.numero})
		if requested_amount > 0:
			note_applications[nota.id] = requested_amount
	return note_applications


def _extract_adjustment_note_request(invoice, post_data, *, field_prefix=''):
	tipo_ajuste = (post_data.get(f'{field_prefix}tipo_ajuste') or 'PRODUCTO').strip().upper()
	tipo_documento = (post_data.get(f'{field_prefix}tipo_documento') or '').strip()
	motivo = (post_data.get(f'{field_prefix}motivo') or '').strip()
	tipo_credito = (post_data.get(f'{field_prefix}tipo_credito') or '').strip()
	descripcion = (post_data.get(f'{field_prefix}descripcion') or '').strip()
	monto = _parse_adjustment_amount(post_data.get(f'{field_prefix}monto'))
	items_payload = []
	has_item_data = False

	if tipo_ajuste not in {'PRODUCTO', 'FINANCIERO'}:
		raise ValidationError(_('Select a valid adjustment type.'))

	if invoice is not None:
		for item in invoice.items.all():
			quantity_value = post_data.get(f'{field_prefix}qty_{item.id}')
			unit_quantity_value = post_data.get(f'{field_prefix}unit_qty_{item.id}')
			amount_value = post_data.get(f'{field_prefix}amount_{item.id}')
			quantity = _parse_non_negative_quantity(quantity_value)
			unit_quantity = _parse_non_negative_quantity(unit_quantity_value)
			amount_text = str(amount_value or '').strip()
			if quantity > 0 or unit_quantity > 0 or amount_text:
				has_item_data = True
			items_payload.append({
				'invoice_item': item,
				'cantidad': quantity,
				'cantidad_unidades': unit_quantity,
				'monto_unitario': amount_value,
			})
	else:
		for presentation_id, quantity_value, unit_quantity_value, amount_value, description in zip(
			post_data.getlist(f'{field_prefix}manual_presentacion'),
			post_data.getlist(f'{field_prefix}manual_qty'),
			post_data.getlist(f'{field_prefix}manual_unit_qty'),
			post_data.getlist(f'{field_prefix}manual_amount'),
			post_data.getlist(f'{field_prefix}manual_description'),
		):
			quantity = _parse_non_negative_quantity(quantity_value)
			unit_quantity = _parse_non_negative_quantity(unit_quantity_value)
			amount_text = str(amount_value or '').strip()
			presentation = None
			if presentation_id:
				presentation = get_object_or_404(Presentacion.objects.select_related('producto'), id=presentation_id)
			if quantity > 0 or unit_quantity > 0 or amount_text or presentation is not None or str(description or '').strip():
				has_item_data = True
			items_payload.append({
				'invoice_item': None,
				'presentacion': presentation,
				'descripcion': (description or '').strip(),
				'cantidad': quantity,
				'cantidad_unidades': unit_quantity,
				'monto_unitario': amount_value,
			})

	has_note_request = bool(tipo_documento or motivo or tipo_credito or descripcion or has_item_data or monto > 0)
	if not has_note_request:
		return None


def _parse_direct_invoice_lines(post_data):
	raw_presentacion_ids = post_data.getlist('presentacion_id')
	raw_quantities = post_data.getlist('cantidad')
	raw_unit_prices = post_data.getlist('precio_unitario')

	line_specs = []
	for index, raw_presentacion_id in enumerate(raw_presentacion_ids):
		presentacion_id = str(raw_presentacion_id or '').strip()
		quantity = str(raw_quantities[index] if index < len(raw_quantities) else '').strip()
		unit_price = str(raw_unit_prices[index] if index < len(raw_unit_prices) else '').strip()
		if not any((presentacion_id, quantity, unit_price)):
			continue
		if not (presentacion_id and quantity and unit_price):
			raise ValidationError(_('Each direct invoice line needs product, quantity, and unit price.'))
		try:
			parsed_presentacion_id = int(presentacion_id)
			parsed_quantity = int(quantity)
			parsed_unit_price = Decimal(unit_price)
		except (TypeError, ValueError, InvalidOperation) as exc:
			raise ValidationError(_('One or more direct invoice lines contain invalid values.')) from exc
		if parsed_quantity <= 0:
			raise ValidationError(_('Quantity must be greater than zero.'))
		if parsed_unit_price <= 0:
			raise ValidationError(_('Unit price must be greater than zero.'))
		line_specs.append({
			'presentacion_id': parsed_presentacion_id,
			'cantidad': parsed_quantity,
			'precio': parsed_unit_price,
		})

	if not line_specs:
		raise ValidationError(_('Add at least one direct invoice line before saving.'))

	presentaciones = {
		presentacion.id: presentacion
		for presentacion in Presentacion.objects.select_related('producto').filter(
			id__in=[spec['presentacion_id'] for spec in line_specs]
		)
	}
	if len(presentaciones) != len({spec['presentacion_id'] for spec in line_specs}):
		raise ValidationError(_('One or more selected presentations no longer exist.'))

	items_payload = []
	for spec in line_specs:
		items_payload.append({
			'presentacion': presentaciones[spec['presentacion_id']],
			'cantidad': spec['cantidad'],
			'precio': spec['precio'],
		})
	return items_payload


def _build_direct_invoice_line_drafts(post_data=None):
	raw_presentacion_ids = post_data.getlist('presentacion_id') if post_data is not None else []
	raw_quantities = post_data.getlist('cantidad') if post_data is not None else []
	raw_unit_prices = post_data.getlist('precio_unitario') if post_data is not None else []
	row_count = max(len(raw_presentacion_ids), len(raw_quantities), len(raw_unit_prices), 1)
	return [{
		'presentacion_id': str(raw_presentacion_ids[index] if index < len(raw_presentacion_ids) else '').strip(),
		'cantidad': str(raw_quantities[index] if index < len(raw_quantities) else '').strip(),
		'precio_unitario': str(raw_unit_prices[index] if index < len(raw_unit_prices) else '').strip(),
	} for index in range(row_count)]


def _build_direct_invoice_context(*, selected_client_id=None, post_data=None):
	selected_client = None
	if selected_client_id:
		selected_client = Cliente.objects.filter(id=selected_client_id).first()
	pending_notes_summary = summarize_pending_customer_notes(cliente=selected_client) if selected_client else None
	default_price_tier = 1
	if selected_client is not None:
		try:
			default_price_tier = max(1, min(5, int(selected_client.nivel_precio or 1)))
		except (TypeError, ValueError):
			default_price_tier = 1
	selected_general_note_values = {}
	if pending_notes_summary and post_data is not None:
		selected_general_note_values = {
			note.id: str(post_data.get(f'general_note_apply_{note.id}') or '').strip()
			for note in pending_notes_summary['notes']
		}
		for note in pending_notes_summary['notes']:
			note.prefill_amount = selected_general_note_values.get(note.id, '')
	return {
		'customers': Cliente.objects.order_by('nombre_empresa'),
		'presentations': Presentacion.objects.select_related('producto').order_by('producto__nombre', 'nombre'),
		'direct_invoice_lines': _build_direct_invoice_line_drafts(post_data),
		'selected_client': selected_client,
		'pending_notes_summary': pending_notes_summary,
		'default_price_tier': default_price_tier,
		'selected_delivery_method': str(post_data.get('metodo_entrega') if post_data is not None else 'CUSTOMER_PICK_UP' or 'CUSTOMER_PICK_UP').strip() or 'CUSTOMER_PICK_UP',
		'backoffice_note_value': str(post_data.get('nota_backoffice') if post_data is not None else '' or '').strip(),
		'use_customer_credit_checked': str(post_data.get('use_customer_credit') if post_data is not None else '').strip().lower() in {'1', 'true', 'on', 'yes'},
		'customer_credit_to_apply_value': str(post_data.get('customer_credit_to_apply') if post_data is not None else '' or '').strip(),
	}
	if not tipo_documento:
		raise ValidationError(_('Select a note type to save the adjustment.'))

	return {
		'tipo_ajuste': tipo_ajuste,
		'tipo_documento': tipo_documento,
		'motivo': motivo,
		'tipo_credito': tipo_credito,
		'descripcion': descripcion,
		'monto': monto,
		'items_payload': items_payload,
	}


def _validate_driver_note_request(*, note_request):
	if note_request is None:
		return
	if note_request['tipo_documento'] != 'CREDITO':
		raise ValidationError(_('Drivers can only request credit notes.'))
	if note_request['tipo_ajuste'] != 'PRODUCTO':
		raise ValidationError(_('Drivers can only request product return credit notes.'))
	if note_request['tipo_credito'] != 'CREDIT_DUMP':
		raise ValidationError(_('Drivers must use Credit Dump for damaged return notes.'))


def _save_adjustment_note_evidence_files(nota, uploaded_files):
	for normalized_file in _normalize_uploaded_files(uploaded_files):
		NotaAjusteEvidencePhoto.objects.create(nota=nota, image=normalized_file)


def _build_note_product_presentations(selected_client):
	price_tier = 1
	if selected_client is not None and int(selected_client.nivel_precio or 0) > 0:
		price_tier = int(selected_client.nivel_precio)
	presentations = list(Presentacion.objects.select_related('producto').order_by('producto__nombre', 'nombre'))
	for presentation in presentations:
		default_amount = presentation.get_price_for_tier(price_tier) or presentation.precio_1
		presentation.note_default_amount = default_amount
	return presentations


def _build_adjustment_note_creation_context(*, selected_client_id=None, selected_invoice_id=None):
	customers = Cliente.objects.order_by('nombre_empresa')
	invoice_queryset = Invoice.objects.select_related('cliente', 'pedido', 'driver').prefetch_related('items__presentacion__producto').filter(estado='GENERADA').order_by('-creada_en')

	selected_invoice = None
	selected_client = None
	available_invoices = Invoice.objects.none()
	customer_general_notes = NotaAjuste.objects.none()

	if selected_invoice_id:
		selected_invoice = get_object_or_404(invoice_queryset, id=selected_invoice_id)
		selected_client = selected_invoice.cliente
		available_invoices = invoice_queryset.filter(cliente=selected_client)
	elif selected_client_id:
		selected_client = get_object_or_404(customers, id=selected_client_id)
		available_invoices = invoice_queryset.filter(cliente=selected_client)

	if selected_client is not None:
		customer_general_notes = (
			NotaAjuste.objects
			.select_related('creada_por', 'aprobada_por')
			.prefetch_related('evidence_photos', 'items__presentacion__producto')
			.filter(cliente=selected_client, invoice__isnull=True)
			.order_by('-creada_en')
		)

	return {
		'customers': customers,
		'available_invoices': available_invoices,
		'customer_general_notes': customer_general_notes,
		'product_presentations': _build_note_product_presentations(selected_client),
		'selected_client': selected_client,
		'selected_invoice': selected_invoice,
		'selected_invoice_quickbooks_locked': bool(selected_invoice and is_sync_locked(selected_invoice)),
	}


def _parse_estimated_delivery_at(value):
	text = str(value or '').strip()
	if not text:
		return None
	parsed = parse_datetime(text)
	if parsed is None:
		raise ValidationError(_('Estimated delivery date must be a valid date and time.'))
	if timezone.is_naive(parsed):
		parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
	return parsed


def _build_invoice_pdf_item_data(invoice):
	items = []
	for item in invoice.items.select_related('presentacion__producto', 'pedido_item').all():
		barcode = _resolve_invoice_barcode(item)
		requested_quantity = item.cantidad_facturada
		if item.pedido_item_id:
			requested_quantity = item.pedido_item.cantidad_solicitada_documentada or item.cantidad_facturada
		suggested_unit_price = _resolve_invoice_suggested_unit_price(item)
		customer_unit_price = Decimal(str(item.precio_unitario or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		if item.presentacion and item.presentacion.unidades:
			customer_unit_price = (customer_unit_price / Decimal(str(item.presentacion.unidades))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		profit_percentage = Decimal('0.00')
		if _is_invoice_suggested_price_default(item, suggested_unit_price):
			profit_percentage = DEFAULT_SUGGESTED_PROFIT_PERCENTAGE.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		elif suggested_unit_price and customer_unit_price and suggested_unit_price > 0:
			try:
				profit_percentage = ((suggested_unit_price - customer_unit_price) / suggested_unit_price * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
			except (ArithmeticError, InvalidOperation, TypeError, ValueError):
				profit_percentage = Decimal('0.00')
		items.append({
			'barcode': barcode,
			'product_name': item.producto_nombre,
			'pack_size': _resolve_invoice_pack_size(item),
			'requested_quantity': str(requested_quantity),
			'dispatched_quantity': str(item.cantidad_facturada),
			'customer_price': _format_pdf_money(item.precio_unitario),
			'suggested_unit_price': _format_pdf_money(suggested_unit_price),
			'subtotal': _format_pdf_money(item.subtotal),
			'profit_percentage': f'{profit_percentage:.2f}%',
		})
	return items


INVOICE_PDF_ITEMS_PER_PAGE = 10


def _chunk_invoice_pdf_item_rows(item_rows, size=INVOICE_PDF_ITEMS_PER_PAGE):
	if size <= 0:
		raise ValueError('Invoice PDF chunk size must be greater than zero.')
	rows = list(item_rows or [])
	if not rows:
		return [[]]
	return [rows[index:index + size] for index in range(0, len(rows), size)]


def _build_invoice_pdf_compact_header(*, styles, invoice_number, generated_on, total_width):
	logo_cell = build_pdf_logo_image(max_width=36, max_height=36)
	if logo_cell:
		logo_cell.hAlign = 'LEFT'
	else:
		logo_cell = Paragraph('<b>LTG</b>', ParagraphStyle(
			'InvoiceCompactFallback',
			parent=styles['BodyText'],
			fontName='Helvetica-Bold',
			fontSize=10,
			leading=11,
			textColor=colors.white,
		))
	text_html = (
		f'<b>La Tortilla Grocery</b><br/>'
		f'Invoice {invoice_number}<br/>'
		f'Generated {generated_on}'
	)
	header = Table(
		[[logo_cell, Paragraph(text_html, ParagraphStyle(
			'InvoiceCompactHeaderText',
			parent=styles['BodyText'],
			fontName='Helvetica',
			fontSize=8,
			leading=9,
			textColor=colors.white,
		))]],
		colWidths=[62, max(total_width - 62, 200)],
	)
	header.setStyle(TableStyle([
		('BACKGROUND', (0, 0), (-1, -1), BRAND_PRIMARY),
		('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
		('LEFTPADDING', (0, 0), (-1, -1), 8),
		('RIGHTPADDING', (0, 0), (-1, -1), 8),
		('TOPPADDING', (0, 0), (-1, -1), 5),
		('BOTTOMPADDING', (0, 0), (-1, -1), 5),
	]))
	return header


def _build_invoice_pdf_barcode(value, *, max_width=66):
	barcode = code128.Code128(value, barHeight=18, barWidth=0.45, humanReadable=True)
	if barcode.width > max_width:
		scaled_bar_width = max(0.2, round(0.45 * (max_width / float(barcode.width)), 3))
		barcode = code128.Code128(value, barHeight=18, barWidth=scaled_bar_width, humanReadable=True)
	barcode.fontName = 'Helvetica'
	barcode.fontSize = 5.5
	barcode.hAlign = 'CENTER'
	barcode_wrapper = Table([[barcode]], colWidths=[max_width])
	barcode_wrapper.setStyle(TableStyle([
		('ALIGN', (0, 0), (-1, -1), 'CENTER'),
		('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
		('LEFTPADDING', (0, 0), (-1, -1), 0),
		('RIGHTPADDING', (0, 0), (-1, -1), 0),
		('TOPPADDING', (0, 0), (-1, -1), 0),
		('BOTTOMPADDING', (0, 0), (-1, -1), 0),
	]))
	return barcode_wrapper


def _build_invoice_pdf_totals_rows(invoice, *, meta_label_style, meta_value_style, section_title_style, body_style):
	return [
		[Paragraph(_('Subtotal'), meta_label_style), Paragraph(_format_pdf_money(invoice.subtotal), meta_value_style)],
		[Paragraph(_('Customer credit applied'), meta_label_style), Paragraph(_format_pdf_money(invoice.credito_cliente_aplicado), meta_value_style)],
		[Paragraph(_('Credit notes'), meta_label_style), Paragraph(_format_pdf_money(invoice.total_creditos), meta_value_style)],
		[Paragraph(_('Debit notes'), meta_label_style), Paragraph(_format_pdf_money(invoice.total_debitos), meta_value_style)],
		[Paragraph(_('Outstanding invoice balance'), meta_label_style), Paragraph(_format_pdf_money(invoice.saldo_cliente), meta_value_style)],
		[Paragraph(_('Final invoice total'), section_title_style), Paragraph(f'<b>{_format_pdf_money(invoice.total_neto)}</b>', body_style)],
	]


def _invoice_pdf_response(invoice):
	buffer = BytesIO()
	document = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=24, rightMargin=24, topMargin=20, bottomMargin=20)
	styles = getSampleStyleSheet()
	meta_label_style = ParagraphStyle('InvoiceMetaLabel', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=7, textColor=BRAND_MUTED_TEXT, leading=9)
	meta_value_style = ParagraphStyle('InvoiceMetaValue', parent=styles['BodyText'], fontSize=8, leading=10, textColor=BRAND_TEXT)
	section_title_style = ParagraphStyle('InvoiceSectionTitle', parent=styles['Heading4'], fontName='Helvetica-Bold', fontSize=8, textColor=BRAND_TEXT, spaceAfter=3)
	note_style = ParagraphStyle('InvoiceNote', parent=styles['BodyText'], fontSize=6.5, textColor=BRAND_MUTED_TEXT, leading=8)
	body_style = ParagraphStyle('InvoiceBody', parent=styles['BodyText'], fontSize=7.5, leading=9, textColor=BRAND_TEXT)
	page_width, _page_height = letter
	content_width = page_width - document.leftMargin - document.rightMargin

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
	item_chunks = _chunk_invoice_pdf_item_rows(item_rows)
	generated_label = timezone.localtime(invoice.creada_en).strftime('%m/%d/%Y %H:%M')

	content = []
	meta_table = Table([
		[Paragraph(_('Customer no.'), meta_label_style), Paragraph(str(invoice.cliente_id), meta_value_style), Paragraph(_('Date'), meta_label_style), Paragraph(timezone.localtime(invoice.creada_en).strftime('%m/%d/%Y'), meta_value_style)],
		[Paragraph(_('Order no.'), meta_label_style), Paragraph(str(invoice.pedido_id), meta_value_style), Paragraph(_('Generated on'), meta_label_style), Paragraph(timezone.localtime(invoice.creada_en).strftime('%m/%d/%Y %H:%M'), meta_value_style)],
		[Paragraph(_('Sales rep'), meta_label_style), Paragraph(sales_rep, meta_value_style), Paragraph(_('Driver'), meta_label_style), Paragraph(driver_name, meta_value_style)],
	], colWidths=[58, 92, 64, 92])
	meta_table.setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('INNERGRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('BACKGROUND', (0, 0), (-1, -1), BRAND_SURFACE),
		('LEFTPADDING', (0, 0), (-1, -1), 6),
		('RIGHTPADDING', (0, 0), (-1, -1), 6),
		('TOPPADDING', (0, 0), (-1, -1), 5),
		('BOTTOMPADDING', (0, 0), (-1, -1), 5),
	]))
	content.extend([
		_build_invoice_pdf_compact_header(styles=styles, invoice_number=invoice.numero, generated_on=generated_label, total_width=content_width),
		Spacer(1, 8),
		meta_table,
		Spacer(1, 8),
	])

	party_table = Table([
		[
			Paragraph(
				f'<b>{_("Sold to")}</b><br/>{invoice.cliente.nombre_empresa}<br/>{invoice.cliente.direccion}<br/>{invoice.cliente.ciudad}, {invoice.cliente.estado} {invoice.cliente.codigo_postal or ""}<br/>{invoice.cliente.pais}',
				body_style,
			),
			Paragraph(
				f'<b>{_("Ship to")}</b><br/>{invoice.cliente.nombre_empresa}<br/>{ship_to}',
				body_style,
			),
			Paragraph(
				f'<b>{_("Terms")}</b><br/>{_("Outstanding invoice balance")}: {_format_pdf_money(invoice.saldo_cliente)}<br/>{_("Final invoice total")}: {_format_pdf_money(invoice.total_neto)}<br/>{_("Customer credit applied")}: {_format_pdf_money(invoice.credito_cliente_aplicado)}<br/>{_("Delivery method")}: {invoice.get_metodo_entrega_display()}',
				body_style,
			),
		],
	], colWidths=[180, 180, 180])
	party_table.setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('INNERGRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('BACKGROUND', (0, 0), (-1, -1), BRAND_SURFACE),
		('VALIGN', (0, 0), (-1, -1), 'TOP'),
		('LEFTPADDING', (0, 0), (-1, -1), 6),
		('RIGHTPADDING', (0, 0), (-1, -1), 6),
		('TOPPADDING', (0, 0), (-1, -1), 5),
		('BOTTOMPADDING', (0, 0), (-1, -1), 5),
	]))
	content.extend([party_table, Spacer(1, 8)])

	content.append(Paragraph(_('Line items with barcode, ordered quantity, dispatched quantity and abbreviated pricing references.'), note_style))
	content.append(Spacer(1, 6))

	for index, chunk in enumerate(item_chunks):
		if index > 0:
			content.extend([
				PageBreak(),
				_build_invoice_pdf_compact_header(styles=styles, invoice_number=invoice.numero, generated_on=generated_label, total_width=content_width),
				Spacer(1, 8),
				Paragraph(_('Continued line items.'), note_style),
				Spacer(1, 6),
			])

		rows = [[_('Barcode'), _('Description'), _('U/M'), _('Qty ord'), _('Qty dsp'), _('Cust. / unit'), _('Subtotal'), _('SGT RTL / SRP 30%')]]
		for item in chunk:
			barcode_cell = Paragraph('-', body_style)
			if item['barcode']:
				barcode_cell = _build_invoice_pdf_barcode(item['barcode'], max_width=80)
			rows.append([
				barcode_cell,
				Paragraph(item['product_name'], body_style),
				Paragraph(item['pack_size'], body_style),
				item['requested_quantity'],
				item['dispatched_quantity'],
				item['customer_price'],
				item['subtotal'],
				item['suggested_unit_price'],
			])

		table = Table(rows, colWidths=[88, 170, 34, 30, 30, 52, 52, 64], repeatRows=1)
		table.setStyle(TableStyle([
			('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
			('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
			('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
			('ALIGN', (0, 0), (-1, 0), 'CENTER'),
			('FONTSIZE', (0, 0), (-1, -1), 7),
			('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
			('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BRAND_SURFACE]),
			('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
			('ALIGN', (3, 1), (-1, -1), 'CENTER'),
			('BOTTOMPADDING', (0, 0), (-1, -1), 5),
			('TOPPADDING', (0, 0), (-1, -1), 5),
			('VALIGN', (0, 1), (0, -1), 'TOP'),
			('TOPPADDING', (0, 1), (0, -1), 2),
			('BOTTOMPADDING', (0, 1), (0, -1), 8),
			('LEFTPADDING', (0, 0), (-1, -1), 4),
			('RIGHTPADDING', (0, 0), (-1, -1), 4),
		]))
		content.append(table)
		if index == len(item_chunks) - 1:
			content.append(Spacer(1, 12))

	totals_table = Table(
		_build_invoice_pdf_totals_rows(
			invoice,
			meta_label_style=meta_label_style,
			meta_value_style=meta_value_style,
			section_title_style=section_title_style,
			body_style=styles['BodyText'],
		),
		colWidths=[92, 110],
	)
	totals_table.setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('INNERGRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('BACKGROUND', (0, 0), (-1, -2), BRAND_SURFACE),
		('BACKGROUND', (0, -1), (-1, -1), BRAND_SOFT_BLUE),
		('LEFTPADDING', (0, 0), (-1, -1), 6),
		('RIGHTPADDING', (0, 0), (-1, -1), 6),
		('TOPPADDING', (0, 0), (-1, -1), 5),
		('BOTTOMPADDING', (0, 0), (-1, -1), 5),
	]))
	content.extend([
		Paragraph(_('Pricing note'), section_title_style),
		Paragraph(_('Suggested retail per unit defaults to a 30% profit suggestion over the customer unit cost. It is a reference for resale, not a mandatory selling price.'), note_style),
		Spacer(1, 6),
		totals_table,
	])

	if hasattr(invoice, 'delivery') and invoice.delivery.firma_cliente:
		try:
			with invoice.delivery.firma_cliente.open('rb') as signature_file:
				signature_bytes = signature_file.read()
		except Exception:
			signature_bytes = None
		if signature_bytes:
			signature_image = Image(BytesIO(signature_bytes), width=180, height=70)
			signature_image.hAlign = 'LEFT'
			content.extend([
				Spacer(1, 12),
				Paragraph(_('Customer signature'), section_title_style),
				Paragraph(_('Signed electronically by the customer during delivery confirmation.'), note_style),
				Spacer(1, 6),
				signature_image,
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


def _ordered_driver_deliveries(queryset):
	return queryset.annotate(
		estimated_delivery_sort=Case(
			When(estimated_delivery_at__isnull=True, then=Value(1)),
			default=Value(0),
			output_field=IntegerField(),
		),
	).order_by('estimated_delivery_sort', 'estimated_delivery_at', 'created_at')


INVOICES_LIST_PAGE_SIZE = 50

INVOICE_QB_STATUS_FILTERS = {
	'due': Q(qb_payment_status__in=['DUE', 'DUE_TODAY']),
	'overdue': Q(qb_payment_status='OVERDUE'),
	'paid': Q(qb_payment_status='PAID'),
	'deposited': Q(qb_payment_status='DEPOSITED'),
	'open': Q(qb_payment_status='OPEN'),
	'not_sent': Q(qb_email_status__in=['NOT_SET', 'NEED_TO_SEND']),
}


def _invoice_qb_status_counts(queryset):
	qb_queryset = queryset.filter(quickbooks_id__isnull=False).exclude(quickbooks_id='')
	return {
		'all': qb_queryset.count(),
		'due': qb_queryset.filter(INVOICE_QB_STATUS_FILTERS['due']).count(),
		'overdue': qb_queryset.filter(INVOICE_QB_STATUS_FILTERS['overdue']).count(),
		'paid': qb_queryset.filter(INVOICE_QB_STATUS_FILTERS['paid']).count(),
		'deposited': qb_queryset.filter(INVOICE_QB_STATUS_FILTERS['deposited']).count(),
		'open': qb_queryset.filter(INVOICE_QB_STATUS_FILTERS['open']).count(),
		'not_sent': qb_queryset.filter(INVOICE_QB_STATUS_FILTERS['not_sent']).count(),
		'unsynced': qb_queryset.filter(qb_payment_status='').count(),
	}


def _invoice_list_view_querysets(*, base_queryset=None):
	queryset = base_queryset if base_queryset is not None else Invoice.objects.all()
	return {
		'pending': queryset.filter(
			estado='GENERADA',
			despachador_notificado=False,
		).exclude(
			delivery__estado__in=['ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'],
		),
		'ready': queryset.filter(
			estado='GENERADA',
			despachador_notificado=True,
		).exclude(
			delivery__estado__in=['ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'],
		),
		'delivered': queryset.filter(
			estado='GENERADA',
			delivery__estado__in=['ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'],
		),
		'cancelled': queryset.filter(estado='ANULADA'),
	}


def _apply_invoice_list_filters(queryset, request):
	query = (request.GET.get('q') or '').strip()
	selected_customer_id = (request.GET.get('cliente_id') or '').strip()
	selected_delivery_method = (request.GET.get('metodo_entrega') or '').strip()
	selected_driver_id = (request.GET.get('driver_id') or '').strip()
	selected_qb_status = (request.GET.get('qb_status') or '').strip().lower()
	date_from_raw = (request.GET.get('date_from') or '').strip()
	date_to_raw = (request.GET.get('date_to') or '').strip()

	valid_delivery_methods = {choice[0] for choice in Invoice.DELIVERY_METHOD_CHOICES}
	if selected_delivery_method not in valid_delivery_methods:
		selected_delivery_method = ''

	if selected_qb_status not in INVOICE_QB_STATUS_FILTERS:
		selected_qb_status = ''

	if query:
		search_filters = (
			Q(numero__icontains=query)
			| Q(cliente__nombre_empresa__icontains=query)
			| Q(driver__username__icontains=query)
			| Q(driver__first_name__icontains=query)
			| Q(driver__last_name__icontains=query)
		)
		if query.isdigit():
			search_filters |= Q(pedido_id=int(query))
		queryset = queryset.filter(search_filters)

	if selected_customer_id:
		queryset = queryset.filter(cliente_id=selected_customer_id)

	if selected_delivery_method:
		queryset = queryset.filter(metodo_entrega=selected_delivery_method)

	if selected_driver_id:
		queryset = queryset.filter(driver_id=selected_driver_id)

	date_from = parse_date(date_from_raw) if date_from_raw else None
	date_to = parse_date(date_to_raw) if date_to_raw else None
	if date_from:
		queryset = queryset.filter(creada_en__date__gte=date_from)
	if date_to:
		queryset = queryset.filter(creada_en__date__lte=date_to)

	if selected_qb_status:
		queryset = queryset.filter(quickbooks_id__isnull=False).exclude(quickbooks_id='').filter(INVOICE_QB_STATUS_FILTERS[selected_qb_status])

	return queryset, {
		'query': query,
		'selected_customer_id': selected_customer_id,
		'selected_delivery_method': selected_delivery_method,
		'selected_driver_id': selected_driver_id,
		'selected_qb_status': selected_qb_status,
		'date_from': date_from_raw if date_from else '',
		'date_to': date_to_raw if date_to else '',
	}


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoices_list(request):
	view_mode = (request.GET.get('view') or 'pending').strip()
	querysets = _invoice_list_view_querysets()
	if view_mode not in querysets:
		view_mode = 'pending'

	filtered_queryset, filter_context = _apply_invoice_list_filters(querysets[view_mode], request)
	filtered_queryset = filtered_queryset.select_related(
		'pedido__cliente',
		'cliente',
		'driver',
		'creada_por',
		'delivery',
	)
	page_obj = Paginator(filtered_queryset, INVOICES_LIST_PAGE_SIZE).get_page(request.GET.get('page'))

	count_querysets = _invoice_list_view_querysets()
	customers = Cliente.objects.filter(invoices__isnull=False).distinct().order_by('nombre_empresa')
	drivers = Usuario.objects.filter(role='driver').order_by('first_name', 'username')
	qb_status_counts = _invoice_qb_status_counts(count_querysets[view_mode])

	return render(request, 'backoffice/invoices_list.html', {
		'page_obj': page_obj,
		'invoices': page_obj,
		'view_mode': view_mode,
		'pending_count': count_querysets['pending'].count(),
		'ready_count': count_querysets['ready'].count(),
		'delivered_count': count_querysets['delivered'].count(),
		'cancelled_count': count_querysets['cancelled'].count(),
		'qb_status_counts': qb_status_counts,
		'customers': customers,
		'drivers': drivers,
		'delivery_method_choices': Invoice.DELIVERY_METHOD_CHOICES,
		**filter_context,
	})


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_direct_invoice_create(request):
	selected_client_id = request.GET.get('cliente_id') or request.POST.get('cliente_id') or ''
	context = _build_direct_invoice_context(
		selected_client_id=selected_client_id or None,
		post_data=request.POST if request.method == 'POST' else None,
	)

	if request.method == 'POST':
		selected_client = context.get('selected_client')
		if selected_client is None:
			messages.error(request, _('Select a customer before creating the direct invoice.'))
			return render(request, 'backoffice/direct_invoice_create.html', context)
		try:
			metodo_entrega = str(request.POST.get('metodo_entrega') or '').strip()
			items_payload = _parse_direct_invoice_lines(request.POST)
			pending_notes_summary = summarize_pending_customer_notes(cliente=selected_client)
			selected_note_applications = _parse_general_note_applications(selected_client, request.POST)
			applied_customer_credit = _parse_customer_credit_to_apply(
				selected_client,
				request.POST,
				available_credit=pending_notes_summary['available_credit_excluding_notes'],
			)
			invoice = generar_invoice_directa_backoffice(
				cliente=selected_client,
				items_payload=items_payload,
				metodo_entrega=metodo_entrega,
				usuario=request.user,
				nota_backoffice=str(request.POST.get('nota_backoffice') or '').strip(),
				applied_customer_credit=applied_customer_credit,
				selected_note_applications=selected_note_applications,
			)
		except ValidationError as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
			return render(request, 'backoffice/direct_invoice_create.html', context)

		messages.success(request, _('Direct invoice generated successfully.'))
		return redirect('backoffice_invoice_detail', invoice_id=invoice.id)

	return render(request, 'backoffice/direct_invoice_create.html', context)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_adjustment_notes_list(request):
	query = (request.GET.get('q') or '').strip()
	selected_customer_id = (request.GET.get('cliente_id') or '').strip()
	selected_creator_role = (request.GET.get('creada_por') or '').strip().lower()
	selected_document_type = (request.GET.get('tipo_documento') or '').strip().upper()
	selected_status = (request.GET.get('estado') or '').strip().upper()
	selected_scope = (request.GET.get('scope') or '').strip().lower()

	valid_creator_roles = {'backoffice', 'driver', 'admin'}
	valid_document_types = {'CREDITO', 'DEBITO'}
	valid_statuses = {'BORRADOR', 'APROBADA', 'ANULADA'}
	valid_scopes = {'invoice', 'general'}

	if selected_creator_role not in valid_creator_roles:
		selected_creator_role = ''
	if selected_document_type not in valid_document_types:
		selected_document_type = ''
	if selected_status not in valid_statuses:
		selected_status = ''
	if selected_scope not in valid_scopes:
		selected_scope = ''

	customers = Cliente.objects.order_by('nombre_empresa')
	notes_queryset = (
		NotaAjuste.objects
		.select_related('cliente', 'invoice', 'creada_por', 'aprobada_por')
		.prefetch_related('items__presentacion__producto', 'evidence_photos')
		.annotate(
			item_count=Count('items', distinct=True),
			evidence_count=Count('evidence_photos', distinct=True),
		)
		.order_by('-creada_en')
	)

	if query:
		notes_queryset = notes_queryset.filter(
			Q(numero__icontains=query)
			| Q(cliente__nombre_empresa__icontains=query)
			| Q(invoice__numero__icontains=query)
			| Q(descripcion__icontains=query)
		)
	if selected_customer_id:
		notes_queryset = notes_queryset.filter(cliente_id=selected_customer_id)
	if selected_creator_role:
		notes_queryset = notes_queryset.filter(creada_por__role=selected_creator_role)
	if selected_document_type:
		notes_queryset = notes_queryset.filter(tipo_documento=selected_document_type)
	if selected_status:
		notes_queryset = notes_queryset.filter(estado=selected_status)
	if selected_scope == 'invoice':
		notes_queryset = notes_queryset.filter(invoice__isnull=False)
	elif selected_scope == 'general':
		notes_queryset = notes_queryset.filter(invoice__isnull=True)

	filtered_notes = list(notes_queryset[:200])
	summary = {
		'total': len(filtered_notes),
		'credits': sum(1 for note in filtered_notes if note.tipo_documento == 'CREDITO'),
		'debits': sum(1 for note in filtered_notes if note.tipo_documento == 'DEBITO'),
		'drafts': sum(1 for note in filtered_notes if note.estado == 'BORRADOR'),
		'invoice_linked': sum(1 for note in filtered_notes if note.invoice_id),
		'general': sum(1 for note in filtered_notes if not note.invoice_id),
		'driver_created': sum(1 for note in filtered_notes if getattr(note.creada_por, 'role', '') == 'driver'),
	}

	return render(request, 'backoffice/adjustment_notes_list.html', {
		'notes': filtered_notes,
		'customers': customers,
		'query': query,
		'selected_customer_id': selected_customer_id,
		'selected_creator_role': selected_creator_role,
		'selected_document_type': selected_document_type,
		'selected_status': selected_status,
		'selected_scope': selected_scope,
		'summary': summary,
	})


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_adjustment_note_create(request):
	selected_client_id = request.GET.get('cliente_id') or request.POST.get('cliente_id') or ''
	selected_invoice_id = request.GET.get('invoice_id') or request.POST.get('invoice_id') or ''
	context = _build_adjustment_note_creation_context(
		selected_client_id=selected_client_id or None,
		selected_invoice_id=selected_invoice_id or None,
	)

	if request.method == 'POST':
		selected_client = context.get('selected_client')
		selected_invoice = context.get('selected_invoice')
		if selected_client is None:
			messages.error(request, _('Select a customer before saving the adjustment note.'))
			return render(request, 'backoffice/adjustment_note_create.html', context)

		try:
			if selected_invoice is not None:
				_validate_invoice_is_not_quickbooks_locked(selected_invoice)
			note_request = _extract_adjustment_note_request(selected_invoice, request.POST, field_prefix='note_')
			if note_request is None:
				raise ValidationError(_('Add note details before saving the adjustment.'))
			nota = crear_nota_ajuste(
				cliente=selected_client,
				invoice=selected_invoice,
				tipo_ajuste=note_request['tipo_ajuste'],
				tipo_documento=note_request['tipo_documento'],
				motivo=note_request['motivo'],
				tipo_credito=note_request['tipo_credito'],
				descripcion=note_request['descripcion'],
				usuario=request.user,
				items_payload=note_request['items_payload'],
				monto=note_request['monto'],
			)
			_save_adjustment_note_evidence_files(nota, request.FILES.getlist('note_evidence_photos'))
		except ValidationError as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
			return render(request, 'backoffice/adjustment_note_create.html', context)

		messages.success(request, _('Adjustment note %(note)s saved as draft.') % {'note': nota.numero})
		if selected_invoice is not None:
			return redirect('backoffice_invoice_detail', invoice_id=selected_invoice.id)
		return redirect(f"{reverse('backoffice_adjustment_note_create')}?cliente_id={selected_client.id}")

	return render(request, 'backoffice/adjustment_note_create.html', context)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_generate_invoice(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	metodo_entrega = request.POST.get('metodo_entrega') or ''
	driver = None
	estimated_delivery_at = None
	driver_id = request.POST.get('driver_id') or ''
	if driver_id:
		driver = get_object_or_404(Usuario, id=driver_id, role='driver', is_active=True)

	try:
		if metodo_entrega == 'RUTA_DRIVER':
			estimated_delivery_at = _parse_estimated_delivery_at(request.POST.get('estimated_delivery_at'))
		suggested_unit_prices = _extract_invoice_suggested_unit_prices(pedido, request.POST)
		pending_notes_summary = summarize_pending_customer_notes(cliente=pedido.cliente)
		selected_note_applications = _parse_general_note_applications(pedido.cliente, request.POST)
		applied_customer_credit = _parse_customer_credit_to_apply(
			pedido.cliente,
			request.POST,
			available_credit=pending_notes_summary['available_credit_excluding_notes'],
		)
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega=metodo_entrega,
			driver=driver,
			usuario=request.user,
			suggested_unit_prices=suggested_unit_prices,
			applied_customer_credit=applied_customer_credit,
			selected_note_applications=selected_note_applications,
			estimated_delivery_at=estimated_delivery_at,
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
		Invoice.objects.select_related('pedido__cliente__usuario', 'driver', 'creada_por').prefetch_related('items__presentacion__producto', 'items__pedido_item__movimientos_inventario', 'items__pedido_item', 'notas_ajuste__items__presentacion', 'notas_ajuste__evidence_photos', 'notas_ajuste__creada_por', 'notas_ajuste__aprobada_por', 'delivery__evidence_photos', 'delivery__notification_logs'),
		id=invoice_id,
	)
	if invoice.metodo_entrega == 'RUTA_DRIVER' and invoice.driver_id:
		ensure_delivery_for_invoice(invoice)
		invoice.refresh_from_db()
	Notificacion.objects.filter(
		tipo='NOTA_AJUSTE',
		url=f'/facturacion/backoffice/invoices/{invoice.id}/',
		leida=False,
	).update(leida=True)
	driver_created_notes_count = invoice.notas_ajuste.filter(creada_por__role='driver').count()
	return render(request, 'backoffice/invoice_detail.html', {
		'invoice': invoice,
		'driver_created_notes_count': driver_created_notes_count,
		'advanced_adjustment_note_url': f"{reverse('backoffice_adjustment_note_create')}?cliente_id={invoice.cliente_id}&invoice_id={invoice.id}",
		'invoice_quickbooks_locked': is_sync_locked(invoice),
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
		_validate_invoice_is_not_quickbooks_locked(invoice)
		note_request = _extract_adjustment_note_request(invoice, request.POST, field_prefix='note_')
		if note_request is None:
			raise ValidationError(_('Add note details before saving the adjustment.'))
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_ajuste=note_request['tipo_ajuste'],
			tipo_documento=note_request['tipo_documento'],
			motivo=note_request['motivo'],
			tipo_credito=note_request['tipo_credito'],
			descripcion=note_request['descripcion'],
			usuario=request.user,
			items_payload=note_request['items_payload'],
			monto=note_request['monto'],
		)
		_save_adjustment_note_evidence_files(nota, request.FILES.getlist('note_evidence_photos'))
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note %(note)s saved as draft.') % {'note': nota.numero})

	return redirect('backoffice_invoice_detail', invoice_id=invoice.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_invoice_approve_note(request, note_id):
	nota = get_object_or_404(NotaAjuste.objects.select_related('invoice', 'cliente'), id=note_id)
	if request.method != 'POST':
		if nota.invoice_id:
			return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)
		return redirect(f"{reverse('backoffice_adjustment_note_create')}?cliente_id={nota.cliente_id}")

	try:
		_validate_note_is_not_quickbooks_locked(nota)
		aprobar_nota_ajuste(nota=nota, usuario=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note approved successfully.'))
	if nota.invoice_id:
		return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)
	return redirect(f"{reverse('backoffice_adjustment_note_create')}?cliente_id={nota.cliente_id}")


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_invoice_cancel_note(request, note_id):
	nota = get_object_or_404(NotaAjuste.objects.select_related('invoice', 'cliente'), id=note_id)
	if request.method != 'POST':
		if nota.invoice_id:
			return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)
		return redirect(f"{reverse('backoffice_adjustment_note_create')}?cliente_id={nota.cliente_id}")

	try:
		_validate_note_is_not_quickbooks_locked(nota)
		anular_nota_ajuste(nota=nota)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note cancelled successfully.'))
	if nota.invoice_id:
		return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)
	return redirect(f"{reverse('backoffice_adjustment_note_create')}?cliente_id={nota.cliente_id}")


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoice_pdf(request, invoice_id):
	invoice = get_object_or_404(Invoice.objects.select_related('pedido__cliente', 'driver').prefetch_related('items__presentacion__producto', 'items__pedido_item__movimientos_inventario', 'items__pedido_item', 'notas_ajuste'), id=invoice_id)
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
		deliveries = _ordered_driver_deliveries(base_queryset.exclude(estado__in=completed_statuses))
	for delivery in deliveries:
		delivery.workflow_badge = build_delivery_workflow_badge(delivery)
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
		Delivery.objects.select_related('invoice__cliente__usuario', 'driver').prefetch_related('invoice__items__pedido_item__movimientos_inventario', 'invoice__items__pedido_item', 'invoice__notas_ajuste__evidence_photos', 'evidence_photos', 'notification_logs'),
		id=delivery_id,
		driver=request.user,
	)
	delivery.workflow_badge = build_delivery_workflow_badge(delivery)
	return render(request, 'backoffice/driver_delivery_detail.html', {
		'delivery': delivery,
		'invoice': delivery.invoice,
		'delivery_collectible_balance': calculate_delivery_collectible_balance(delivery=delivery),
	})


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

	deliveries = _ordered_driver_deliveries(Delivery.objects.filter(
		driver=request.user,
		estado__in={'ASIGNADA', 'EN_RUTA'},
		id__in=selected_delivery_ids,
	))
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
@transaction.atomic
def driver_delivery_complete(request, delivery_id):
	delivery = get_object_or_404(
		Delivery.objects.select_related('invoice__cliente__usuario').prefetch_related('invoice__items__presentacion__producto', 'invoice__notas_ajuste__items'),
		id=delivery_id,
		driver=request.user,
	)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	try:
		nota = None
		note_request = _extract_adjustment_note_request(delivery.invoice, request.POST, field_prefix='driver_note_')
		_validate_driver_note_request(note_request=note_request)
		note_evidence_files = _normalize_uploaded_files(request.FILES.getlist('driver_note_evidence_photos'))
		if note_request is None and note_evidence_files:
			raise ValidationError(_('Select a note type before uploading adjustment evidence.'))
		if note_request is not None:
			nota = crear_nota_ajuste_desde_invoice(
				invoice=delivery.invoice,
				tipo_ajuste=note_request['tipo_ajuste'],
				tipo_documento=note_request['tipo_documento'],
				motivo=note_request['motivo'],
				tipo_credito=note_request['tipo_credito'],
				descripcion=note_request['descripcion'],
				usuario=request.user,
				items_payload=note_request['items_payload'],
				monto=note_request['monto'],
			)
		complete_driver_delivery(
			delivery=delivery,
			driver_user=request.user,
			payload=request.POST,
			evidence_files=_normalize_uploaded_files(request.FILES.getlist('evidence_photos')),
			payment_files=request.FILES,
			cheque_image_file=request.FILES.get('cheque_imagen'),
			adjustment_note=nota,
		)
		if nota is not None:
			_save_adjustment_note_evidence_files(nota, note_evidence_files)
	except ValidationError as exc:
		transaction.set_rollback(True)
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Delivery saved successfully.'))
		if nota is not None:
			messages.success(request, _('Adjustment note %(note)s saved as draft for BackOffice review.') % {'note': nota.numero})
	return redirect('driver_delivery_detail', delivery_id=delivery.id)


@login_required
@internal_permission_required('driver.delivery.manage')
@transaction.atomic
def driver_delivery_create_note(request, delivery_id):
	delivery = get_object_or_404(
		Delivery.objects.select_related('invoice__cliente__usuario').prefetch_related('invoice__items__presentacion__producto', 'invoice__notas_ajuste__items', 'invoice__notas_ajuste__evidence_photos'),
		id=delivery_id,
		driver=request.user,
	)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	if not delivery.is_completed:
		messages.error(request, _('You can only create adjustment notes after completing the delivery.'))
		return redirect('driver_delivery_detail', delivery_id=delivery.id)

	try:
		note_request = _extract_adjustment_note_request(delivery.invoice, request.POST, field_prefix='driver_note_')
		_validate_driver_note_request(note_request=note_request)
		note_evidence_files = _normalize_uploaded_files(request.FILES.getlist('driver_note_evidence_photos'))
		if note_request is None:
			raise ValidationError(_('Add note details before saving the adjustment.'))
		nota = crear_nota_ajuste_desde_invoice(
			invoice=delivery.invoice,
			tipo_ajuste=note_request['tipo_ajuste'],
			tipo_documento=note_request['tipo_documento'],
			motivo=note_request['motivo'],
			tipo_credito=note_request['tipo_credito'],
			descripcion=note_request['descripcion'],
			usuario=request.user,
			items_payload=note_request['items_payload'],
			monto=note_request['monto'],
		)
		_save_adjustment_note_evidence_files(nota, note_evidence_files)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note %(note)s saved as draft for BackOffice review.') % {'note': nota.numero})
	return redirect('driver_delivery_detail', delivery_id=delivery.id)


@login_required
@internal_permission_required('driver.delivery.view')
def driver_invoice_pdf(request, delivery_id):
	delivery = get_object_or_404(Delivery.objects.select_related('invoice__cliente', 'invoice__driver'), id=delivery_id, driver=request.user)
	invoice = Invoice.objects.select_related('pedido__cliente', 'driver').prefetch_related('items__presentacion__producto', 'items__pedido_item__movimientos_inventario', 'items__pedido_item', 'notas_ajuste').get(id=delivery.invoice_id)
	return _invoice_pdf_response(invoice)
