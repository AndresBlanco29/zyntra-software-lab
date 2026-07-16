from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.clientes.credit_limit import (
	CreditLimitBlockedError,
	CreditLimitExceededError,
	create_credit_limit_alert,
	evaluate_customer_credit_limit,
	notify_credit_limit_alert,
	validate_credit_limit_for_pedido_invoice,
)
from config.clientes.models import Cliente
from config.core.datetime_formats import format_local_date, format_local_datetime
from config.core.product_ordering import order_invoice_items_for_display
from config.core.workflow_badges import build_delivery_workflow_badge
from config.productos.models import Presentacion
from config.core.pdf_branding import (
	BRAND_BORDER,
	BRAND_MUTED_TEXT,
	BRAND_PRIMARY,
	BRAND_SOFT_BLUE,
	BRAND_SURFACE,
	BRAND_TEXT,
	NumberedPdfCanvas,
	build_pdf_logo_image,
)
from config.integrations.quickbooks.sync import is_sync_locked, resolve_customer_company_name
from config.notificaciones.models import Notificacion
from config.pedidos.models import Pedido
from config.usuarios.models import Usuario
from config.usuarios.permissions import internal_permission_required

from .form_drafts import (
	DELIVERY_COMPLETE_DRAFT_SCOPE,
	DELIVERY_NOTE_DRAFT_SCOPE,
	INVOICE_ADJUSTMENT_DRAFT_SCOPE,
	INVOICE_PICKUP_DRAFT_SCOPE,
	clear_invoice_workflow_drafts,
	clear_workflow_draft,
	get_workflow_draft,
	merge_post_into_workflow_draft,
	remove_post_prefix_from_workflow_draft,
	serialize_post_data,
)
from .models import Delivery, DeliveryEvidencePhoto, FacturacionRegistroAnulacion, Invoice, NotaAjuste, NotaAjusteEvidencePhoto
from .services import (
	DEFAULT_SUGGESTED_PROFIT_PERCENTAGE,
	_normalize_uploaded_file,
	_normalize_uploaded_files,
	aprobar_nota_ajuste,
	anular_invoice,
	anular_nota_ajuste,
	attach_invoice_item_net_dispatched_quantities,
	build_google_maps_route_url,
	build_invoice_shipment_summary,
	calculate_delivery_collectible_balance,
	complete_customer_pickup_from_backoffice,
	complete_driver_delivery,
	crear_nota_ajuste,
	crear_nota_ajuste_desde_invoice,
	eliminar_invoice,
	invoice_delete_requires_confirmation_phrase,
	validate_invoice_delete_confirmation_phrase,
	_invoice_allows_quickbooks_bypass_on_delete,
	eliminar_nota_ajuste,
	ensure_delivery_for_invoice,
	ensure_customer_pickup_delivery_for_invoice,
	generar_invoice_desde_picking,
	generar_invoice_directa_backoffice,
	list_pending_customer_notes,
	resolve_invoice_payment_due_date,
	resolve_presentacion_suggested_unit_price,
	resolve_driver_credit_type_from_motivo,
	summarize_pending_customer_notes,
	start_delivery_route,
	unlock_client_from_delivery,
	mark_delivery_unpaid_from_backoffice,
	resolve_customer_amount_owed,
	resolve_customer_open_balance,
	resolve_customer_overdue_balance,
	user_can_operate_driver_delivery,
	user_can_oversee_driver_deliveries,
)


def _format_pdf_money(value):
	amount = Decimal(str(value or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
	return f'${amount:,.2f}'


def _build_invoice_pdf_terms_paragraph(invoice, body_style):
	cliente = invoice.cliente
	lines = [f'<b>{_("Terms")}</b>']
	payment_terms_label = cliente.get_terminos_pago_label()
	if payment_terms_label:
		lines.append(f'<b>{payment_terms_label}</b>')
	overdue_balance = resolve_customer_overdue_balance(cliente=cliente)
	if overdue_balance > 0:
		lines.append(f'<b>{_("DUE BALANCE")}</b>: {_format_pdf_money(overdue_balance)}')
	elif cliente.balance < 0:
		lines.append(f'<b>{_("CUSTOMER CREDIT")}</b>: {_format_pdf_money(cliente.customer_credit_balance)}')
	return Paragraph('<br/>'.join(lines), body_style)


def _resolve_invoice_pdf_due_date_label(invoice):
	due_date = resolve_invoice_payment_due_date(invoice)
	if due_date is None:
		return '-'
	return format_local_date(due_date)


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
		return item.presentacion_nombre_display
	return f'{item.presentacion_nombre_display} x {presentacion.unidades}'


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


def _parse_invoice_line_discount_percentage(value):
	from config.facturacion.services import _parse_line_discount_percentage
	return _parse_line_discount_percentage(value)


def _extract_invoice_line_discounts(pedido, post_data):
	line_discounts = {}
	for item in pedido.items.all():
		raw_discount = post_data.get(f'line_discount_percentage_{item.id}')
		if raw_discount in (None, ''):
			continue
		line_discounts[item.id] = _parse_invoice_line_discount_percentage(raw_discount)
	return line_discounts


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

	if not tipo_documento:
		raise ValidationError(_('Select a note type to save the adjustment.'))

	if tipo_documento == 'CREDITO' and not tipo_credito and tipo_ajuste == 'PRODUCTO':
		tipo_credito = 'CREDIT_DUMP'

	return {
		'tipo_ajuste': tipo_ajuste,
		'tipo_documento': tipo_documento,
		'motivo': motivo,
		'tipo_credito': tipo_credito,
		'descripcion': descripcion,
		'monto': monto,
		'items_payload': items_payload,
	}


def _prepare_driver_note_request(note_request):
	if note_request is None:
		return None
	if note_request['tipo_documento'] != 'CREDITO':
		raise ValidationError(_('Drivers can only request credit notes.'))
	if note_request['tipo_ajuste'] != 'PRODUCTO':
		raise ValidationError(_('Drivers can only request product return credit notes.'))
	if not note_request.get('motivo'):
		raise ValidationError(_('Select a reason to save the adjustment.'))
	note_request['tipo_credito'] = resolve_driver_credit_type_from_motivo(note_request['motivo'])
	return note_request


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


def _build_adjustment_note_creation_context(*, selected_client_id=None, selected_invoice_id=None, customer_query=''):
	customers = Cliente.objects.order_by('nombre_empresa')
	query = str(customer_query or '').strip()
	filtered_customers = customers
	if query:
		filtered_customers = customers.filter(nombre_empresa__icontains=query)
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
		if query and not filtered_customers.filter(pk=selected_client.pk).exists():
			filtered_customers = (
				Cliente.objects.filter(pk=selected_client.pk)
				| filtered_customers
			).distinct().order_by('nombre_empresa')

	if selected_client is not None:
		customer_general_notes = (
			NotaAjuste.objects
			.select_related('creada_por', 'aprobada_por')
			.prefetch_related('evidence_photos', 'items__presentacion__producto')
			.filter(cliente=selected_client, invoice__isnull=True)
			.order_by('-creada_en')
		)

	selected_invoice_items = []
	if selected_invoice is not None:
		selected_invoice_items = order_invoice_items_for_display(selected_invoice)
		attach_invoice_item_net_dispatched_quantities(selected_invoice, selected_invoice_items)

	return {
		'customers': filtered_customers,
		'customer_search_query': query,
		'customer_search_options': list(customers.values('id', 'nombre_empresa')),
		'available_invoices': available_invoices,
		'customer_general_notes': customer_general_notes,
		'product_presentations': _build_note_product_presentations(selected_client),
		'selected_client': selected_client,
		'selected_invoice': selected_invoice,
		'selected_invoice_items': selected_invoice_items,
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


def _calculate_invoice_line_discount_total(invoice):
	total = Decimal('0.00')
	for item in invoice.items.all():
		discount_amount_unit = Decimal(str(item.descuento_monto_unitario or 0))
		if discount_amount_unit > 0:
			quantity = Decimal(str(item.cantidad_facturada or 0))
			total += discount_amount_unit * quantity
			continue
		discount_percentage = Decimal(str(item.descuento_porcentaje or 0))
		if discount_percentage <= 0 or not item.precio_unitario_lista:
			continue
		list_price = Decimal(str(item.precio_unitario_lista))
		final_price = Decimal(str(item.precio_unitario or 0))
		quantity = Decimal(str(item.cantidad_facturada or 0))
		total += (list_price - final_price) * quantity
	return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _build_invoice_pdf_item_data(invoice):
	items = []
	display_items = order_invoice_items_for_display(invoice)
	attach_invoice_item_net_dispatched_quantities(invoice, display_items)
	for item in display_items:
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
		discount_percentage = Decimal(str(item.descuento_porcentaje or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		discount_amount_unit = Decimal(str(item.descuento_monto_unitario or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		if item.precio_unitario_lista is not None:
			list_price_value = Decimal(str(item.precio_unitario_lista))
		else:
			list_price_value = Decimal(str(item.precio_unitario or 0))
		line_discount_amount = Decimal('0.00')
		if discount_amount_unit > 0:
			line_discount_amount = (discount_amount_unit * Decimal(str(item.cantidad_facturada or 0))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		elif discount_percentage > 0 and list_price_value is not None:
			line_discount_amount = ((list_price_value - Decimal(str(item.precio_unitario or 0))) * Decimal(str(item.cantidad_facturada or 0))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
		items.append({
			'barcode': barcode,
			'product_name': item.producto_nombre,
			'pack_size': INVOICE_PDF_UNIT_OF_MEASURE,
			'requested_quantity': str(requested_quantity),
			'dispatched_quantity': str(item.cantidad_despachada_neta),
			'list_price': _format_pdf_money(list_price_value),
			'discount_amount_unit': _format_pdf_money(discount_amount_unit) if discount_amount_unit > 0 else '—',
			'discount_percentage': f'{discount_percentage:.2f}%' if discount_percentage > 0 else '—',
			'line_discount_amount': _format_pdf_money(line_discount_amount) if line_discount_amount > 0 else '—',
			'customer_price': _format_pdf_money(item.precio_unitario),
			'suggested_unit_price': _format_pdf_money(suggested_unit_price),
			'subtotal': _format_pdf_money(item.subtotal),
			'profit_percentage': f'{profit_percentage:.2f}%',
		})
	return items


INVOICE_PDF_ITEMS_PER_PAGE = 20  # Legacy helper size; invoice PDF uses one continuous table.
INVOICE_PDF_UNIT_OF_MEASURE = 'CS'
INVOICE_PDF_SHOW_SUGGESTED_RETAIL = False
INVOICE_PDF_BARCODE_BAR_HEIGHT = 22
INVOICE_PDF_BARCODE_FONT_SIZE = 6
INVOICE_PDF_BARCODE_CELL_HEIGHT = INVOICE_PDF_BARCODE_BAR_HEIGHT + INVOICE_PDF_BARCODE_FONT_SIZE + 3
INVOICE_PDF_ITEM_COLUMN_WEIGHTS_BASE = (104, 108, 36, 26, 26, 46, 32, 46, 46)
INVOICE_PDF_SUGGESTED_RETAIL_COLUMN_WEIGHT = 84


def _invoice_pdf_item_column_weights():
	if INVOICE_PDF_SHOW_SUGGESTED_RETAIL:
		return INVOICE_PDF_ITEM_COLUMN_WEIGHTS_BASE + (INVOICE_PDF_SUGGESTED_RETAIL_COLUMN_WEIGHT,)
	return INVOICE_PDF_ITEM_COLUMN_WEIGHTS_BASE


def _invoice_pdf_item_table_column_widths(content_width):
	total_weight = sum(_invoice_pdf_item_column_weights())
	return [content_width * weight / total_weight for weight in _invoice_pdf_item_column_weights()]


def _build_invoice_pdf_item_table_header(header_style):
	columns = [
		Paragraph(_('Barcode'), header_style),
		Paragraph(_('Description'), header_style),
		Paragraph(_('U/M'), header_style),
		Paragraph(_('QtyOrd'), header_style),
		Paragraph(_('Qtyshp'), header_style),
		Paragraph(_('price'), header_style),
		Paragraph(_('Disc/prom'), header_style),
		Paragraph(_('net price'), header_style),
		Paragraph(_('total'), header_style),
	]
	if INVOICE_PDF_SHOW_SUGGESTED_RETAIL:
		columns.append(Paragraph(_('SGT RTL<br/>/ SRP 30%'), header_style))
	return columns


def _chunk_invoice_pdf_item_rows(item_rows, size=INVOICE_PDF_ITEMS_PER_PAGE):
	if size <= 0:
		raise ValueError('Invoice PDF chunk size must be greater than zero.')
	rows = list(item_rows or [])
	if not rows:
		return [[]]
	return [rows[index:index + size] for index in range(0, len(rows), size)]


def _get_invoice_pdf_company_contact_lines():
	from config.core.models import HomeContenido

	contenido = HomeContenido.objects.filter(activo=True).order_by('-actualizado').first()
	if contenido:
		lines = [
			contenido.footer_contacto_direccion_linea_1,
			contenido.footer_contacto_direccion_linea_2,
			contenido.footer_contacto_email,
			contenido.footer_contacto_telefono,
		]
	else:
		lines = [
			'1666 Roswell Rd Bldg 100',
			'Marietta, GA 30062-3639',
			'latortilla@gmail.com',
			'+1 (470) 967 2782',
		]
	return [line.strip() for line in lines if line and str(line).strip()]


def _build_invoice_pdf_compact_header(*, styles, invoice_number, total_width):
	from html import escape

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
	header_text_style = ParagraphStyle(
		'InvoiceCompactHeaderText',
		parent=styles['BodyText'],
		fontName='Helvetica',
		fontSize=7,
		leading=8,
		textColor=colors.white,
	)
	invoice_number_style = ParagraphStyle(
		'InvoiceCompactHeaderNumber',
		parent=styles['BodyText'],
		fontName='Helvetica-Bold',
		fontSize=9,
		leading=10,
		textColor=colors.white,
		alignment=TA_RIGHT,
	)
	contact_html = '<br/>'.join(escape(line) for line in _get_invoice_pdf_company_contact_lines())
	company_html = f'<b>La Tortilla Grocery</b><br/>{contact_html}'
	invoice_number_width = 150
	center_width = max(total_width - 62 - invoice_number_width, 200)
	header = Table(
		[[
			logo_cell,
			Paragraph(company_html, header_text_style),
			Paragraph(escape(invoice_number), invoice_number_style),
		]],
		colWidths=[62, center_width, invoice_number_width],
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
	default_bar_width = 0.6
	barcode = code128.Code128(
		value,
		barHeight=INVOICE_PDF_BARCODE_BAR_HEIGHT,
		barWidth=default_bar_width,
		humanReadable=True,
	)
	if barcode.width > max_width:
		scaled_bar_width = max(0.32, round(default_bar_width * (max_width / float(barcode.width)), 3))
		barcode = code128.Code128(
			value,
			barHeight=INVOICE_PDF_BARCODE_BAR_HEIGHT,
			barWidth=scaled_bar_width,
			humanReadable=True,
		)
	barcode.fontName = 'Helvetica'
	barcode.fontSize = INVOICE_PDF_BARCODE_FONT_SIZE
	barcode.hAlign = 'CENTER'
	return barcode


def _build_invoice_pdf_barcode_cell(value, *, max_width=66, placeholder_style):
	if value:
		cell_content = _build_invoice_pdf_barcode(value, max_width=max_width)
	else:
		cell_content = Paragraph('-', placeholder_style)
	barcode_wrapper = Table(
		[[cell_content]],
		colWidths=[max_width],
		rowHeights=[INVOICE_PDF_BARCODE_CELL_HEIGHT],
	)
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
	rows = [
		[Paragraph(_('Subtotal'), meta_label_style), Paragraph(_format_pdf_money(invoice.subtotal), meta_value_style)],
	]
	line_discount_total = _calculate_invoice_line_discount_total(invoice)
	if line_discount_total > 0:
		rows.append([
			Paragraph(_('Discounts applied'), meta_label_style),
			Paragraph(f'-{_format_pdf_money(line_discount_total)}', meta_value_style),
		])
	rows.extend([
		[Paragraph(_('Customer credit applied'), meta_label_style), Paragraph(_format_pdf_money(invoice.credito_cliente_aplicado), meta_value_style)],
		[Paragraph(_('Credit notes'), meta_label_style), Paragraph(_format_pdf_money(invoice.total_creditos), meta_value_style)],
		[Paragraph(_('Debit notes'), meta_label_style), Paragraph(_format_pdf_money(invoice.total_debitos), meta_value_style)],
		[Paragraph(_('Total invoice'), section_title_style), Paragraph(f'<b>{_format_pdf_money(invoice.total_neto)}</b>', body_style)],
	])
	return rows


def _format_pdf_weight(value):
	amount = Decimal(str(value or '0')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
	return f'{amount:,.1f}'


def _build_invoice_pdf_shipment_summary_table(summary, *, box_style, value_style, total_width=None):
	pallets_value = summary.get('total_pallets')
	if pallets_value is None:
		pallets_label = '-'
	else:
		pallets_label = str(pallets_value)

	if total_width is None:
		col_widths = [78, 48, 68, 58, 62, 52]
	else:
		col_widths = [
			total_width * 0.26,
			total_width * 0.10,
			total_width * 0.20,
			total_width * 0.14,
			total_width * 0.16,
			total_width * 0.14,
		]
	summary_table = Table(
		[[
			Paragraph('<b>No. of Cases:</b>', box_style),
			Paragraph(str(summary['total_cases']), value_style),
			Paragraph('<b>Total WGT:</b>', box_style),
			Paragraph(_format_pdf_weight(summary['total_weight']), value_style),
			Paragraph('<b>Pallets:</b>', box_style),
			Paragraph(pallets_label, value_style),
		]],
		colWidths=col_widths,
		hAlign='LEFT',
	)
	summary_table.setStyle(TableStyle([
		('BOX', (0, 0), (0, 0), 0.75, BRAND_BORDER),
		('BOX', (2, 0), (2, 0), 0.75, BRAND_BORDER),
		('BOX', (4, 0), (4, 0), 0.75, BRAND_BORDER),
		('BACKGROUND', (0, 0), (0, 0), colors.white),
		('BACKGROUND', (2, 0), (2, 0), colors.white),
		('BACKGROUND', (4, 0), (4, 0), colors.white),
		('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
		('LEFTPADDING', (0, 0), (-1, -1), 4),
		('RIGHTPADDING', (0, 0), (-1, -1), 4),
		('TOPPADDING', (0, 0), (-1, -1), 4),
		('BOTTOMPADDING', (0, 0), (-1, -1), 4),
	]))
	return summary_table


def _build_invoice_pdf_signature_flowables(invoice, *, section_title_style, body_style, note_style, signature_width):
	signature_title_style = ParagraphStyle(
		'InvoiceSignatureTitle',
		parent=section_title_style,
		fontSize=11,
		leading=14,
		spaceAfter=4,
	)
	flowables = [
		Paragraph("Customer's Signature:", signature_title_style),
	]
	signature_bytes = None
	delivery = getattr(invoice, 'delivery', None)
	if delivery and getattr(delivery, 'firma_cliente', None):
		try:
			with invoice.delivery.firma_cliente.open('rb') as signature_file:
				signature_bytes = signature_file.read()
		except Exception:
			signature_bytes = None
	if signature_bytes:
		image_width = min(signature_width, 240)
		signature_image = Image(BytesIO(signature_bytes), width=image_width, height=52)
		signature_image.hAlign = 'LEFT'
		flowables.extend([
			Spacer(1, 18),
			signature_image,
		])
	else:
		signature_line = Table([['']], colWidths=[signature_width], rowHeights=[26])
		signature_line.setStyle(TableStyle([
			('LINEBELOW', (0, 0), (-1, -1), 0.75, BRAND_TEXT),
			('TOPPADDING', (0, 0), (-1, -1), 16),
			('BOTTOMPADDING', (0, 0), (-1, -1), 0),
		]))
		flowables.extend([
			Spacer(1, 18),
			signature_line,
		])
	flowables.extend([
		Spacer(1, 6),
		Paragraph(
			'In case of default of payment, the Customer agrees to pay all costs of collection and legal fees. '
			'Past due balances will be subject to late payment fees and interest at the maximum rate permitted by law. '
			'Pursuant to Article 2 of the Uniform Commercial Code (UCC), the seller retains a security interest in all '
			'goods delivered until payment is received in full.',
			note_style,
		),
		Spacer(1, 2),
		Paragraph(
			'The perishable agricultural commodities listed on this invoice are sold subject to the statutory trust authorized by section 5(c) of the Perishable Agricultural Commodities Act, 1930 (7 U.S.C. 499e(c)). The seller of these commodities retains a trust claim over these commodities, all inventories of food or other products derived from these commodities, and any receivables or proceeds from the sale of these commodities until full payment is received.',
			note_style,
		),
	])
	return flowables


def _build_invoice_pdf_footer_layout(
	invoice,
	*,
	content_width,
	left_width,
	meta_label_style,
	meta_value_style,
	section_title_style,
	body_style,
	note_style,
):
	summary_box_style = ParagraphStyle(
		'InvoiceSummaryBox',
		parent=body_style,
		fontName='Helvetica-Bold',
		fontSize=8,
		leading=10,
		textColor=BRAND_TEXT,
	)
	shipment_summary = build_invoice_shipment_summary(invoice)
	left_rows = [[
		_build_invoice_pdf_shipment_summary_table(
			shipment_summary,
			box_style=summary_box_style,
			value_style=body_style,
			total_width=left_width,
		),
	]]
	for flowable in _build_invoice_pdf_signature_flowables(
		invoice,
		section_title_style=section_title_style,
		body_style=body_style,
		note_style=note_style,
		signature_width=max(left_width - 8, 180),
	):
		left_rows.append([flowable])
	left_column = Table(left_rows, colWidths=[left_width])
	left_column.setStyle(TableStyle([
		('VALIGN', (0, 0), (-1, -1), 'TOP'),
		('LEFTPADDING', (0, 0), (-1, -1), 0),
		('RIGHTPADDING', (0, 0), (-1, -1), 0),
		('TOPPADDING', (0, 0), (-1, -1), 0),
		('BOTTOMPADDING', (0, 0), (-1, -1), 0),
		('TOPPADDING', (0, 1), (0, 1), 8),
	]))

	totals_col_widths = [92, 110]
	totals_table = Table(
		_build_invoice_pdf_totals_rows(
			invoice,
			meta_label_style=meta_label_style,
			meta_value_style=meta_value_style,
			section_title_style=section_title_style,
			body_style=body_style,
		),
		colWidths=totals_col_widths,
		hAlign='RIGHT',
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

	right_width = max(content_width - left_width, sum(totals_col_widths))
	footer_table = Table(
		[[left_column, totals_table]],
		colWidths=[left_width, right_width],
		hAlign='LEFT',
	)
	footer_table.setStyle(TableStyle([
		('VALIGN', (0, 0), (-1, -1), 'TOP'),
		('ALIGN', (0, 0), (0, 0), 'LEFT'),
		('ALIGN', (1, 0), (1, 0), 'RIGHT'),
		('LEFTPADDING', (0, 0), (-1, -1), 0),
		('RIGHTPADDING', (0, 0), (-1, -1), 0),
		('TOPPADDING', (0, 0), (-1, -1), 0),
		('BOTTOMPADDING', (0, 0), (-1, -1), 0),
	]))
	return footer_table


def _invoice_pdf_response(invoice):
	buffer = BytesIO()
	document = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=24, rightMargin=24, topMargin=20, bottomMargin=20)
	styles = getSampleStyleSheet()
	meta_label_style = ParagraphStyle('InvoiceMetaLabel', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=7, textColor=BRAND_MUTED_TEXT, leading=9)
	meta_value_style = ParagraphStyle('InvoiceMetaValue', parent=styles['BodyText'], fontSize=8, leading=10, textColor=BRAND_TEXT)
	section_title_style = ParagraphStyle('InvoiceSectionTitle', parent=styles['Heading4'], fontName='Helvetica-Bold', fontSize=8, textColor=BRAND_TEXT, spaceAfter=3)
	note_style = ParagraphStyle('InvoiceNote', parent=styles['BodyText'], fontSize=6.5, textColor=BRAND_MUTED_TEXT, leading=8)
	body_style = ParagraphStyle('InvoiceBody', parent=styles['BodyText'], fontSize=7.5, leading=9, textColor=BRAND_TEXT)
	table_header_style = ParagraphStyle(
		'InvoiceTableHeader',
		parent=styles['BodyText'],
		fontName='Helvetica-Bold',
		fontSize=6.5,
		leading=7.5,
		textColor=colors.white,
		alignment=TA_CENTER,
	)
	table_cell_center_style = ParagraphStyle(
		'InvoiceTableCellCenter',
		parent=body_style,
		fontSize=7,
		leading=8,
		alignment=TA_CENTER,
	)
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
	customer_company_name = resolve_customer_company_name(invoice.cliente)
	item_rows = _build_invoice_pdf_item_data(invoice)
	item_column_widths = _invoice_pdf_item_table_column_widths(content_width)
	left_footer_width = item_column_widths[0] + item_column_widths[1]
	generated_label = format_local_datetime(invoice.creada_en)

	content = []
	meta_table = Table([
		[
			Paragraph(_('Customer no.'), meta_label_style), Paragraph(str(invoice.cliente_id), meta_value_style),
			Paragraph(_('Order no.'), meta_label_style), Paragraph(str(invoice.pedido_id), meta_value_style),
			Paragraph(_('Generated'), meta_label_style), Paragraph(generated_label, meta_value_style),
		],
		[
			Paragraph(_('Sales rep'), meta_label_style), Paragraph(sales_rep, meta_value_style),
			Paragraph(_('Driver'), meta_label_style), Paragraph(driver_name, meta_value_style),
			Paragraph(_('Due date'), meta_label_style), Paragraph(_resolve_invoice_pdf_due_date_label(invoice), meta_value_style),
		],
	], colWidths=[58, 70, 48, 70, 58, 80])
	meta_table.setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('INNERGRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('BACKGROUND', (0, 0), (-1, -1), BRAND_SURFACE),
		('LEFTPADDING', (0, 0), (-1, -1), 4),
		('RIGHTPADDING', (0, 0), (-1, -1), 4),
		('TOPPADDING', (0, 0), (-1, -1), 3),
		('BOTTOMPADDING', (0, 0), (-1, -1), 3),
	]))
	content.extend([
		_build_invoice_pdf_compact_header(styles=styles, invoice_number=invoice.numero, total_width=content_width),
		Spacer(1, 6),
		meta_table,
		Spacer(1, 6),
	])

	party_table = Table([
		[
			Paragraph(
				f'<b>{_("Sold to")}</b><br/>{customer_company_name}<br/>{invoice.cliente.direccion}<br/>{invoice.cliente.ciudad}, {invoice.cliente.estado} {invoice.cliente.codigo_postal or ""}<br/>{invoice.cliente.pais}',
				body_style,
			),
			Paragraph(
				f'<b>{_("Ship to")}</b><br/>{customer_company_name}<br/>{ship_to}',
				body_style,
			),
			_build_invoice_pdf_terms_paragraph(invoice, body_style),
		],
	], colWidths=[180, 180, 180])
	party_table.setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('INNERGRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('BACKGROUND', (0, 0), (-1, -1), BRAND_SURFACE),
		('VALIGN', (0, 0), (-1, -1), 'TOP'),
		('LEFTPADDING', (0, 0), (-1, -1), 6),
		('RIGHTPADDING', (0, 0), (-1, -1), 6),
		('TOPPADDING', (0, 0), (-1, -1), 4),
		('BOTTOMPADDING', (0, 0), (-1, -1), 4),
	]))
	content.extend([party_table, Spacer(1, 4)])

	# One continuous item table so ReportLab fills each page from the top.
	# Forced page-breaks every N rows left half-empty continuation sheets.
	rows = [_build_invoice_pdf_item_table_header(table_header_style)]
	barcode_column_width = item_column_widths[0] - 8
	for item in item_rows:
		barcode_cell = _build_invoice_pdf_barcode_cell(
			item['barcode'],
			max_width=max(barcode_column_width, 58),
			placeholder_style=body_style,
		)
		row = [
			barcode_cell,
			Paragraph(item['product_name'], body_style),
			Paragraph(item['pack_size'], body_style),
			Paragraph(item['requested_quantity'], table_cell_center_style),
			Paragraph(item['dispatched_quantity'], table_cell_center_style),
			Paragraph(item['list_price'], table_cell_center_style),
			Paragraph(item['discount_amount_unit'], table_cell_center_style),
			Paragraph(item['customer_price'], table_cell_center_style),
			Paragraph(item['subtotal'], table_cell_center_style),
		]
		if INVOICE_PDF_SHOW_SUGGESTED_RETAIL:
			row.append(Paragraph(item['suggested_unit_price'], table_cell_center_style))
		rows.append(row)

	if len(rows) == 1:
		# Keep a header-only placeholder when the invoice has no lines.
		rows.append([
			Paragraph('-', body_style),
			Paragraph('-', body_style),
			Paragraph('-', body_style),
			Paragraph('0', table_cell_center_style),
			Paragraph('0', table_cell_center_style),
			Paragraph(_format_pdf_money(0), table_cell_center_style),
			Paragraph('—', table_cell_center_style),
			Paragraph(_format_pdf_money(0), table_cell_center_style),
			Paragraph(_format_pdf_money(0), table_cell_center_style),
		])
		if INVOICE_PDF_SHOW_SUGGESTED_RETAIL:
			rows[-1].append(Paragraph(_format_pdf_money(0), table_cell_center_style))

	table = Table(rows, colWidths=item_column_widths, repeatRows=1)
	table.setStyle(TableStyle([
		('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
		('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
		('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
		('ALIGN', (0, 0), (-1, 0), 'CENTER'),
		('FONTSIZE', (0, 1), (-1, -1), 7),
		('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BRAND_SURFACE]),
		('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
		('BOTTOMPADDING', (0, 0), (-1, -1), 2),
		('TOPPADDING', (0, 0), (-1, -1), 2),
		('TOPPADDING', (0, 0), (-1, 0), 4),
		('BOTTOMPADDING', (0, 0), (-1, 0), 4),
		('VALIGN', (0, 1), (0, -1), 'TOP'),
		('TOPPADDING', (0, 1), (0, -1), 1),
		('BOTTOMPADDING', (0, 1), (0, -1), 4),
		('LEFTPADDING', (0, 0), (-1, -1), 4),
		('RIGHTPADDING', (0, 0), (-1, -1), 4),
	]))
	content.extend([table, Spacer(1, 8)])

	content.append(_build_invoice_pdf_footer_layout(
		invoice,
		content_width=content_width,
		left_width=left_footer_width,
		meta_label_style=meta_label_style,
		meta_value_style=meta_value_style,
		section_title_style=section_title_style,
		body_style=body_style,
		note_style=note_style,
	))

	document.build(
		content,
		canvasmaker=lambda *args, **kwargs: NumberedPdfCanvas(
			*args,
			page_label_template=_('Page %(current)s of %(total)s'),
			**kwargs,
		),
	)
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
		'customer_name': resolve_customer_company_name(delivery.invoice.cliente),
		'driver_name': (delivery.driver.get_full_name() or delivery.driver.username) if delivery.driver else _('Customer pick up'),
		'status': delivery.get_estado_display(),
		'payment_status': delivery.get_estado_pago_display(),
		'has_location': delivery.has_live_location,
		'latitude': float(delivery.current_latitude) if delivery.current_latitude is not None else None,
		'longitude': float(delivery.current_longitude) if delivery.current_longitude is not None else None,
		'accuracy_meters': float(delivery.current_accuracy_meters) if delivery.current_accuracy_meters is not None else None,
		'speed_mps': float(delivery.current_speed_mps) if delivery.current_speed_mps is not None else None,
		'heading': float(delivery.current_heading) if delivery.current_heading is not None else None,
		'location_updated_at': location_updated.isoformat() if location_updated else None,
		'location_updated_label': format_local_datetime(location_updated, seconds=True) if location_updated else '-',
		'location_age_seconds': location_age_seconds,
		'route_started_label': format_local_datetime(delivery.route_started_at) if delivery.route_started_at else '-',
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


def _driver_deliveries_base_queryset_for_user(user):
	queryset = Delivery.objects.select_related(
		'invoice__cliente__usuario',
		'driver',
	).prefetch_related('invoice__items')
	if user_can_oversee_driver_deliveries(user):
		return queryset.filter(driver__isnull=False)
	return queryset.filter(driver=user)


def _get_operable_delivery_or_404(delivery_id, user, *, select_related=None, prefetch_related=None):
	queryset = Delivery.objects.all()
	if select_related:
		queryset = queryset.select_related(*select_related)
	if prefetch_related:
		queryset = queryset.prefetch_related(*prefetch_related)
	delivery = get_object_or_404(queryset, id=delivery_id)
	if not user_can_operate_driver_delivery(delivery=delivery, user=user):
		raise Http404('No Delivery matches the given query.')
	return delivery


INVOICES_LIST_PAGE_SIZE = 50

QUICKBOOKS_IMPORTED_PEDIDO = Q(pedido__canal_toma='QUICKBOOKS_IMPORT')

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
			QUICKBOOKS_IMPORTED_PEDIDO,
		).exclude(
			delivery__estado__in=['ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'],
		),
		'ready': queryset.filter(
			estado='GENERADA',
		).filter(
			Q(despachador_notificado=True) | QUICKBOOKS_IMPORTED_PEDIDO,
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
	recent_cancelled_invoices = list(
		count_querysets['cancelled']
		.select_related('cliente', 'anulada_por')
		.order_by('-anulada_en', '-id')[:5]
	)

	return render(request, 'backoffice/invoices_list.html', {
		'page_obj': page_obj,
		'invoices': page_obj,
		'view_mode': view_mode,
		'pending_count': count_querysets['pending'].count(),
		'ready_count': count_querysets['ready'].count(),
		'delivered_count': count_querysets['delivered'].count(),
		'cancelled_count': count_querysets['cancelled'].count(),
		'recent_cancelled_invoices': recent_cancelled_invoices,
		'qb_status_counts': qb_status_counts,
		'customers': customers,
		'drivers': drivers,
		'delivery_method_choices': Invoice.DELIVERY_METHOD_CHOICES,
		'can_create_direct_invoice': request.user.has_internal_permission('backoffice.orders.manage'),
		**filter_context,
	})


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
		customer_query=request.GET.get('q'),
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
			context['form_draft'] = serialize_post_data(request.POST)
			return render(request, 'backoffice/adjustment_note_create.html', context)

		messages.success(request, _('Adjustment note %(note)s saved as draft.') % {'note': nota.numero})
		if selected_invoice is not None:
			return redirect('backoffice_invoice_detail', invoice_id=selected_invoice.id)
		return redirect(f"{reverse('backoffice_adjustment_note_create')}?cliente_id={selected_client.id}")

	return render(request, 'backoffice/adjustment_note_create.html', context)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_create_direct_invoice(request):
	customers = Cliente.objects.filter(aprobado=True).order_by('nombre_empresa')
	drivers = Usuario.objects.filter(role='driver', is_active=True).order_by('first_name', 'last_name', 'username')
	form_state = {
		'cliente_id': (request.POST.get('cliente_id') if request.method == 'POST' else request.GET.get('cliente_id')) or '',
		'metodo_entrega': (request.POST.get('metodo_entrega') if request.method == 'POST' else '') or '',
		'driver_id': (request.POST.get('driver_id') if request.method == 'POST' else '') or '',
		'estimated_delivery_at': (request.POST.get('estimated_delivery_at') if request.method == 'POST' else '') or '',
		'nota_backoffice': (request.POST.get('nota_backoffice') if request.method == 'POST' else '') or '',
	}

	if request.method == 'POST':
		try:
			cliente_id = (request.POST.get('cliente_id') or '').strip()
			if not cliente_id.isdigit():
				raise ValidationError(_('Select a customer before creating the direct invoice.'))
			cliente = get_object_or_404(Cliente, id=int(cliente_id), aprobado=True)
			if getattr(cliente, 'credit_hold', False):
				raise ValidationError(_('This customer is on credit hold. The direct invoice cannot be created.'))

			items_payload = _parse_direct_invoice_items_payload(request.POST)
			estimated_total = sum(
				(
					Decimal(str(item['precio']))
					* Decimal(str(item['cantidad']))
					* (Decimal('1') - (Decimal(str(item.get('descuento_porcentaje') or 0)) / Decimal('100')))
				).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
				for item in items_payload
			)
			evaluation = evaluate_customer_credit_limit(cliente=cliente, additional_amount=estimated_total)
			if evaluation.exceeds_limit:
				raise ValidationError(
					_(
						'This customer would exceed the credit limit with this invoice. '
						'Remaining limit: $%(remaining)s. Exceeded by: $%(excess)s.'
					) % {
						'remaining': evaluation.remaining_limit,
						'excess': evaluation.excess_amount,
					}
				)

			metodo_entrega = (request.POST.get('metodo_entrega') or '').strip()
			driver = None
			estimated_delivery_at = None
			if metodo_entrega == 'RUTA_DRIVER':
				driver_id = (request.POST.get('driver_id') or '').strip()
				if not driver_id.isdigit():
					raise ValidationError(_('A driver is required for route deliveries.'))
				driver = get_object_or_404(Usuario, id=int(driver_id), role='driver', is_active=True)
				estimated_delivery_at = _parse_estimated_delivery_at(request.POST.get('estimated_delivery_at'))

			invoice = generar_invoice_directa_backoffice(
				cliente=cliente,
				items_payload=items_payload,
				metodo_entrega=metodo_entrega,
				usuario=request.user,
				nota_backoffice=(request.POST.get('nota_backoffice') or '').strip(),
				driver=driver,
				estimated_delivery_at=estimated_delivery_at,
			)
		except ValidationError as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		else:
			messages.success(request, _('Direct invoice created successfully.'))
			return redirect(f"{reverse('backoffice_invoice_detail', args=[invoice.id])}?focus_adjustment_note=1")

	return render(request, 'backoffice/invoice_create_direct.html', {
		'customers': customers,
		'drivers': drivers,
		'form_state': form_state,
		'posted_lines': _direct_invoice_posted_lines(request.POST) if request.method == 'POST' else [],
	})


def _direct_invoice_money(value, *, default='0'):
	try:
		return Decimal(str(value if value not in (None, '') else default)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
	except (InvalidOperation, TypeError, ValueError):
		raise ValidationError(_('One or more direct invoice lines contain invalid values.'))


def _direct_invoice_posted_lines(post_data):
	indices = []
	for key in post_data.keys():
		if not key.startswith('line_presentacion_id_'):
			continue
		suffix = key[len('line_presentacion_id_'):]
		if suffix.isdigit():
			indices.append(int(suffix))
	lines = []
	for index in sorted(set(indices)):
		presentacion_id = (post_data.get(f'line_presentacion_id_{index}') or '').strip()
		label = (post_data.get(f'line_label_{index}') or '').strip()
		qty = (post_data.get(f'line_cantidad_{index}') or '').strip()
		price = (post_data.get(f'line_precio_{index}') or '').strip()
		discount = (post_data.get(f'line_descuento_porcentaje_{index}') or '').strip()
		if not presentacion_id and not qty and not price:
			continue
		lines.append({
			'presentacion_id': presentacion_id,
			'label': label,
			'cantidad': qty,
			'precio': price,
			'descuento_porcentaje': discount or '0',
		})
	return lines


def _parse_direct_invoice_items_payload(post_data):
	posted_lines = _direct_invoice_posted_lines(post_data)
	if not posted_lines:
		raise ValidationError(_('Add at least one item before creating the direct invoice.'))

	items_payload = []
	for line in posted_lines:
		presentacion_id = (line.get('presentacion_id') or '').strip()
		if not presentacion_id.isdigit():
			raise ValidationError(_('Each direct invoice line needs product, quantity, and unit price.'))
		try:
			cantidad = int(line.get('cantidad') or 0)
		except (TypeError, ValueError):
			raise ValidationError(_('One or more direct invoice lines contain invalid values.'))
		if cantidad < 1:
			raise ValidationError(_('Each direct invoice line needs product, quantity, and unit price.'))
		precio = _direct_invoice_money(line.get('precio'))
		if precio <= 0:
			raise ValidationError(_('Each direct invoice line needs product, quantity, and unit price.'))
		descuento_porcentaje = _direct_invoice_money(line.get('descuento_porcentaje') or 0)
		if descuento_porcentaje < 0 or descuento_porcentaje >= 100:
			raise ValidationError(_('One or more direct invoice lines contain invalid values.'))
		presentacion = get_object_or_404(Presentacion.objects.select_related('producto'), id=int(presentacion_id))
		items_payload.append({
			'presentacion': presentacion,
			'cantidad': cantidad,
			'precio': precio,
			'descuento_porcentaje': descuento_porcentaje,
		})
	return items_payload


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_generate_invoice(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	metodo_entrega = (request.POST.get('metodo_entrega') or '').strip()
	driver = None
	estimated_delivery_at = None
	if metodo_entrega == 'RUTA_DRIVER':
		driver_id = request.POST.get('driver_id') or ''
		if driver_id:
			driver = get_object_or_404(Usuario, id=driver_id, role='driver', is_active=True)
		estimated_delivery_at = _parse_estimated_delivery_at(request.POST.get('estimated_delivery_at'))

	try:
		suggested_unit_prices = _extract_invoice_suggested_unit_prices(pedido, request.POST)
		line_discounts = _extract_invoice_line_discounts(pedido, request.POST)
		pending_notes_summary = summarize_pending_customer_notes(cliente=pedido.cliente)
		selected_note_applications = _parse_general_note_applications(pedido.cliente, request.POST)
		applied_customer_credit = _parse_customer_credit_to_apply(
			pedido.cliente,
			request.POST,
			available_credit=pending_notes_summary['available_credit_excluding_notes'],
		)
		validate_credit_limit_for_pedido_invoice(pedido=pedido, request_amount=pedido.total)
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega=metodo_entrega,
			driver=driver,
			usuario=request.user,
			suggested_unit_prices=suggested_unit_prices,
			line_discounts=line_discounts,
			applied_customer_credit=applied_customer_credit,
			selected_note_applications=selected_note_applications,
			estimated_delivery_at=estimated_delivery_at,
		)
	except CreditLimitBlockedError as exc:
		messages.error(
			request,
			_('This order is blocked because the customer exceeded the credit limit. Remaining limit: $%(remaining)s. Exceeded by: $%(excess)s.') % {
				'remaining': exc.evaluation.remaining_limit,
				'excess': exc.evaluation.excess_amount,
			},
		)
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)
	except CreditLimitExceededError as exc:
		alerta = create_credit_limit_alert(cliente=pedido.cliente, pedido=pedido, evaluation=exc.evaluation)
		notify_credit_limit_alert(alerta=alerta, pedido_id=pedido.id)
		messages.warning(
			request,
			_('The customer exceeded the credit limit. Review the alert and choose Release or Block before generating the invoice.'),
		)
		return redirect(f"{reverse('backoffice_pedido_detalle', args=[pedido.id])}?credit_limit_alert=1")
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	messages.success(request, _('Invoice generated successfully.'))
	return redirect(f"{reverse('backoffice_invoice_detail', args=[invoice.id])}?focus_adjustment_note=1")


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
	pickup_delivery = None
	show_customer_pickup_completion = False
	pickup_collectible_balance = None
	can_complete_pickup = False
	if invoice.metodo_entrega == 'CUSTOMER_PICK_UP' and invoice.estado == 'GENERADA' and not is_sync_locked(invoice):
		pickup_delivery = ensure_customer_pickup_delivery_for_invoice(invoice)
		invoice.refresh_from_db()
		if pickup_delivery and not pickup_delivery.is_completed:
			can_complete_pickup = request.user.has_internal_permission('backoffice.orders.manage')
			show_customer_pickup_completion = can_complete_pickup
			pickup_collectible_balance = calculate_delivery_collectible_balance(delivery=pickup_delivery)
	Notificacion.objects.filter(
		tipo='NOTA_AJUSTE',
		url=f'/facturacion/backoffice/invoices/{invoice.id}/',
		leida=False,
	).update(leida=True)
	driver_created_notes_count = invoice.notas_ajuste.filter(creada_por__role='driver').count()
	focus_adjustment_note = str(request.GET.get('focus_adjustment_note') or '').strip() == '1'
	can_create_adjustment_note = not is_sync_locked(invoice) and invoice.estado != 'ANULADA'
	show_prominent_adjustment_note = can_create_adjustment_note and not show_customer_pickup_completion
	invoice_items = list(order_invoice_items_for_display(invoice))
	attach_invoice_item_net_dispatched_quantities(invoice, invoice_items)
	attach_invoice_item_net_dispatched_quantities(invoice, list(invoice.items.all()))
	customer_overdue_balance = resolve_customer_overdue_balance(cliente=invoice.cliente)
	customer_open_balance = resolve_customer_open_balance(cliente=invoice.cliente)
	return render(request, 'backoffice/invoice_detail.html', {
		'invoice': invoice,
		'customer_company_name': resolve_customer_company_name(invoice.cliente),
		'invoice_items': invoice_items,
		'invoice_shipment_summary': build_invoice_shipment_summary(invoice),
		'invoice_payment_due_date': resolve_invoice_payment_due_date(invoice),
		'customer_overdue_balance': customer_overdue_balance,
		'customer_open_balance': customer_open_balance,
		'customer_amount_owed': customer_overdue_balance,
		'driver_created_notes_count': driver_created_notes_count,
		'advanced_adjustment_note_url': f"{reverse('backoffice_adjustment_note_create')}?cliente_id={invoice.cliente_id}&invoice_id={invoice.id}",
		'invoice_quickbooks_locked': is_sync_locked(invoice),
		'can_void_invoice': invoice.can_void_from_backoffice(),
		'can_delete_invoice': invoice.can_delete_from_backoffice(),
		'invoice_requires_delete_confirmation': invoice.requires_delete_confirmation_phrase(),
		'void_registro': invoice.registros_anulacion.order_by('-anulado_en', '-id').first(),
		'focus_adjustment_note': focus_adjustment_note,
		'show_prominent_adjustment_note': show_prominent_adjustment_note,
		'show_customer_pickup_completion': show_customer_pickup_completion,
		'pickup_delivery': pickup_delivery,
		'pickup_collectible_balance': pickup_collectible_balance,
		'can_complete_pickup': can_complete_pickup,
		'pickup_form_draft': get_workflow_draft(request.session, INVOICE_PICKUP_DRAFT_SCOPE, invoice.id),
		'adjustment_note_form_draft': get_workflow_draft(request.session, INVOICE_ADJUSTMENT_DRAFT_SCOPE, invoice.id),
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

	field_prefix = (request.POST.get('adjustment_field_prefix') or 'note_').strip()
	if field_prefix not in {'note_', 'driver_note_'}:
		field_prefix = 'note_'

	try:
		_validate_invoice_is_not_quickbooks_locked(invoice)
		evidence_field = 'driver_note_evidence_photos' if field_prefix == 'driver_note_' else 'note_evidence_photos'
		note_request = _extract_adjustment_note_request(invoice, request.POST, field_prefix=field_prefix)
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
		_save_adjustment_note_evidence_files(nota, request.FILES.getlist(evidence_field))
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		if field_prefix == 'driver_note_':
			merge_post_into_workflow_draft(
				request.session,
				INVOICE_PICKUP_DRAFT_SCOPE,
				invoice.id,
				request.POST,
			)
		else:
			merge_post_into_workflow_draft(
				request.session,
				INVOICE_ADJUSTMENT_DRAFT_SCOPE,
				invoice.id,
				request.POST,
			)
	else:
		messages.success(request, _('Adjustment note %(note)s saved as draft.') % {'note': nota.numero})
		if field_prefix == 'driver_note_':
			remove_post_prefix_from_workflow_draft(
				request.session,
				INVOICE_PICKUP_DRAFT_SCOPE,
				invoice.id,
				'driver_note_',
			)
		else:
			clear_workflow_draft(request.session, INVOICE_ADJUSTMENT_DRAFT_SCOPE, invoice.id)

	return redirect('backoffice_invoice_detail', invoice_id=invoice.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
@transaction.atomic
def backoffice_invoice_complete_pickup(request, invoice_id):
	invoice = get_object_or_404(
		Invoice.objects.select_related('cliente', 'pedido').prefetch_related('items__presentacion__producto', 'notas_ajuste__items'),
		id=invoice_id,
	)
	if invoice.metodo_entrega != 'CUSTOMER_PICK_UP':
		messages.error(request, _('This invoice is not configured for customer pick up.'))
		return redirect('backoffice_invoice_detail', invoice_id=invoice.id)
	if request.method != 'POST':
		return redirect('backoffice_invoice_detail', invoice_id=invoice.id)
	if is_sync_locked(invoice):
		messages.error(request, _('This invoice is locked after QuickBooks sync.'))
		return redirect('backoffice_invoice_detail', invoice_id=invoice.id)
	try:
		nota = None
		note_request = _extract_adjustment_note_request(invoice, request.POST, field_prefix='driver_note_')
		note_evidence_files = _normalize_uploaded_files(request.FILES.getlist('driver_note_evidence_photos'))
		if note_request is None and note_evidence_files:
			raise ValidationError(_('Select a note type before uploading adjustment evidence.'))
		if note_request is not None:
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
		complete_customer_pickup_from_backoffice(
			invoice=invoice,
			backoffice_user=request.user,
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
		merge_post_into_workflow_draft(
			request.session,
			INVOICE_PICKUP_DRAFT_SCOPE,
			invoice.id,
			request.POST,
		)
	else:
		messages.success(request, _('Customer pick up completed successfully.'))
		if nota is not None:
			messages.success(request, _('Adjustment note %(note)s saved as draft.') % {'note': nota.numero})
		clear_invoice_workflow_drafts(request.session, invoice.id)
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

	next_url = str(request.POST.get('next') or '').strip()
	if not next_url:
		if nota.invoice_id:
			next_url = reverse('backoffice_invoice_detail', kwargs={'invoice_id': nota.invoice_id})
		else:
			next_url = f"{reverse('backoffice_adjustment_note_create')}?cliente_id={nota.cliente_id}"

	try:
		_validate_note_is_not_quickbooks_locked(nota)
		motivo = str(request.POST.get('motivo') or '').strip()
		anular_nota_ajuste(nota=nota, usuario=request.user, motivo=motivo)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note voided successfully. Inventory and balances were reversed when applicable.'))
	return redirect(next_url)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_invoice_delete_note(request, note_id):
	nota = get_object_or_404(NotaAjuste.objects.select_related('invoice', 'cliente'), id=note_id)
	if request.method != 'POST':
		if nota.invoice_id:
			return redirect('backoffice_invoice_detail', invoice_id=nota.invoice_id)
		return redirect(f"{reverse('backoffice_adjustment_note_create')}?cliente_id={nota.cliente_id}")

	next_url = str(request.POST.get('next') or '').strip()
	if not next_url:
		if nota.invoice_id:
			next_url = reverse('backoffice_invoice_detail', kwargs={'invoice_id': nota.invoice_id})
		else:
			next_url = f"{reverse('backoffice_adjustment_note_create')}?cliente_id={nota.cliente_id}"

	try:
		eliminar_nota_ajuste(nota=nota)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Adjustment note deleted permanently.'))
	return redirect(next_url)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_invoice_void(request, invoice_id):
	invoice = get_object_or_404(Invoice.objects.select_related('pedido', 'cliente'), id=invoice_id)
	if request.method != 'POST':
		return redirect('backoffice_invoice_detail', invoice_id=invoice.id)

	next_url = str(request.POST.get('next') or '').strip()
	if not next_url:
		next_url = reverse('backoffice_invoice_detail', kwargs={'invoice_id': invoice.id})

	try:
		_validate_invoice_is_not_quickbooks_locked(invoice)
		motivo = str(request.POST.get('motivo') or '').strip()
		anular_invoice(invoice=invoice, usuario=request.user, motivo=motivo)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Invoice voided successfully. Products were returned to inventory and a void record was saved.'))
		if 'view=delivered' in next_url:
			next_url = f"{reverse('backoffice_invoices_list')}?view=cancelled"
	return redirect(next_url)


def _resolve_invoice_delete_force_quickbooks(request, invoice):
	validate_invoice_delete_confirmation_phrase(
		invoice=invoice,
		confirmation_phrase=request.POST.get('confirmation_phrase'),
	)
	if invoice_delete_requires_confirmation_phrase(invoice):
		return True
	return _invoice_allows_quickbooks_bypass_on_delete(invoice)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_invoice_delete(request, invoice_id):
	invoice = get_object_or_404(Invoice.objects.select_related('pedido', 'cliente'), id=invoice_id)
	if request.method != 'POST':
		return redirect('backoffice_invoice_detail', invoice_id=invoice.id)

	next_url = str(request.POST.get('next') or '').strip() or reverse('backoffice_invoices_list')

	try:
		force_quickbooks = _resolve_invoice_delete_force_quickbooks(request, invoice)
		if not force_quickbooks:
			_validate_invoice_is_not_quickbooks_locked(invoice)
		eliminar_invoice(invoice=invoice, force_quickbooks=force_quickbooks)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect(next_url)

	messages.success(request, _('Invoice deleted permanently. Products were returned to inventory when applicable.'))
	return redirect(next_url)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_void_records_list(request):
	registros = (
		FacturacionRegistroAnulacion.objects
		.select_related('cliente', 'anulado_por', 'invoice', 'nota')
		.order_by('-anulado_en', '-id')[:200]
	)
	return render(request, 'backoffice/void_records_list.html', {'registros': registros})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoice_pdf(request, invoice_id):
	invoice = get_object_or_404(Invoice.objects.select_related('pedido__cliente', 'driver', 'delivery').prefetch_related('items__presentacion__producto', 'items__pedido_item__movimientos_inventario', 'items__pedido_item', 'notas_ajuste'), id=invoice_id)
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
@internal_permission_required('backoffice.orders.manage')
def backoffice_mark_delivery_unpaid(request, delivery_id):
	delivery = get_object_or_404(Delivery.objects.select_related('invoice__cliente'), id=delivery_id)
	if request.method != 'POST':
		return redirect('backoffice_invoice_detail', invoice_id=delivery.invoice_id)
	try:
		mark_delivery_unpaid_from_backoffice(
			delivery=delivery,
			backoffice_user=request.user,
			motivo_no_pago=request.POST.get('motivo_no_pago'),
		)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Delivery payment status updated to unpaid.'))
	return redirect('backoffice_invoice_detail', invoice_id=delivery.invoice_id)


@login_required
@internal_permission_required('driver.delivery.view')
def driver_delivery_list(request):
	view_mode = request.GET.get('view')
	completed_statuses = {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}
	can_oversee = user_can_oversee_driver_deliveries(request.user)
	base_queryset = _driver_deliveries_base_queryset_for_user(request.user)
	if can_oversee:
		driver_filter = (request.GET.get('driver_id') or '').strip()
		if driver_filter.isdigit():
			base_queryset = base_queryset.filter(driver_id=int(driver_filter))
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
		'can_oversee_driver_deliveries': can_oversee,
	})


@login_required
@internal_permission_required('driver.delivery.view')
def driver_delivery_detail(request, delivery_id):
	delivery = _get_operable_delivery_or_404(
		delivery_id,
		request.user,
		select_related=('invoice__cliente__usuario', 'driver'),
		prefetch_related=(
			'invoice__items__pedido_item__movimientos_inventario',
			'invoice__items__pedido_item',
			'invoice__notas_ajuste__evidence_photos',
			'evidence_photos',
			'notification_logs',
		),
	)
	delivery.workflow_badge = build_delivery_workflow_badge(delivery)
	attach_invoice_item_net_dispatched_quantities(delivery.invoice, list(delivery.invoice.items.all()))
	return render(request, 'backoffice/driver_delivery_detail.html', {
		'delivery': delivery,
		'invoice': delivery.invoice,
		'delivery_collectible_balance': calculate_delivery_collectible_balance(delivery=delivery),
		'delivery_complete_form_draft': get_workflow_draft(request.session, DELIVERY_COMPLETE_DRAFT_SCOPE, delivery.id),
		'delivery_note_form_draft': get_workflow_draft(request.session, DELIVERY_NOTE_DRAFT_SCOPE, delivery.id),
		'can_oversee_driver_deliveries': user_can_oversee_driver_deliveries(request.user),
	})


@login_required
@internal_permission_required('driver.delivery.manage')
def driver_delivery_upload_evidence(request, delivery_id):
	delivery = _get_operable_delivery_or_404(
		delivery_id,
		request.user,
		select_related=('invoice__cliente__usuario', 'driver'),
	)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)

	evidence_files = request.FILES.getlist('evidence_photos')
	detail_url = reverse('driver_delivery_detail', args=[delivery.id]) + '#driver-evidence'
	if not evidence_files:
		messages.error(request, _('Select at least one evidence photo to upload.'))
		return HttpResponseRedirect(detail_url)

	for uploaded_file in evidence_files:
		DeliveryEvidencePhoto.objects.create(delivery=delivery, image=uploaded_file)

	messages.success(request, _('Evidence photos uploaded successfully.'))
	return HttpResponseRedirect(detail_url)


@login_required
@internal_permission_required('driver.delivery.manage')
def driver_delivery_tracking(request, delivery_id):
	delivery = _get_operable_delivery_or_404(
		delivery_id,
		request.user,
		select_related=('invoice__cliente__usuario', 'driver'),
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

	delivery = _get_operable_delivery_or_404(
		delivery_id,
		request.user,
		select_related=('invoice', 'driver'),
	)
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
	delivery = _get_operable_delivery_or_404(
		delivery_id,
		request.user,
		select_related=('invoice__cliente', 'driver'),
	)
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

	route_qs = _driver_deliveries_base_queryset_for_user(request.user).filter(
		estado__in={'ASIGNADA', 'EN_RUTA'},
		id__in=selected_delivery_ids,
	)
	deliveries = _ordered_driver_deliveries(route_qs)
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
	delivery = _get_operable_delivery_or_404(
		delivery_id,
		request.user,
		select_related=('invoice__cliente__usuario', 'driver'),
		prefetch_related=('invoice__items__presentacion__producto', 'invoice__notas_ajuste__items'),
	)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	try:
		nota = None
		note_request = _prepare_driver_note_request(
			_extract_adjustment_note_request(delivery.invoice, request.POST, field_prefix='driver_note_'),
		)
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
		merge_post_into_workflow_draft(
			request.session,
			DELIVERY_COMPLETE_DRAFT_SCOPE,
			delivery.id,
			request.POST,
		)
	else:
		messages.success(request, _('Delivery saved successfully. You can continue with the next stop.'))
		if nota is not None:
			messages.success(request, _('Adjustment note %(note)s saved as draft for BackOffice review.') % {'note': nota.numero})
		clear_workflow_draft(request.session, DELIVERY_COMPLETE_DRAFT_SCOPE, delivery.id)
		return redirect('driver_delivery_list')
	return redirect('driver_delivery_detail', delivery_id=delivery.id)


@login_required
@internal_permission_required('driver.delivery.manage')
@transaction.atomic
def driver_delivery_create_note(request, delivery_id):
	delivery = _get_operable_delivery_or_404(
		delivery_id,
		request.user,
		select_related=('invoice__cliente__usuario', 'driver'),
		prefetch_related=(
			'invoice__items__presentacion__producto',
			'invoice__notas_ajuste__items',
			'invoice__notas_ajuste__evidence_photos',
		),
	)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	if not delivery.is_completed:
		messages.error(request, _('You can only create adjustment notes after completing the delivery.'))
		return redirect('driver_delivery_detail', delivery_id=delivery.id)

	try:
		note_request = _prepare_driver_note_request(
			_extract_adjustment_note_request(delivery.invoice, request.POST, field_prefix='driver_note_'),
		)
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
		merge_post_into_workflow_draft(
			request.session,
			DELIVERY_NOTE_DRAFT_SCOPE,
			delivery.id,
			request.POST,
		)
	else:
		messages.success(request, _('Adjustment note %(note)s saved as draft for BackOffice review.') % {'note': nota.numero})
		clear_workflow_draft(request.session, DELIVERY_NOTE_DRAFT_SCOPE, delivery.id)
	return redirect('driver_delivery_detail', delivery_id=delivery.id)


@login_required
@internal_permission_required('driver.delivery.view')
def driver_invoice_pdf(request, delivery_id):
	delivery = _get_operable_delivery_or_404(
		delivery_id,
		request.user,
		select_related=('invoice__cliente', 'invoice__driver', 'driver'),
	)
	invoice = Invoice.objects.select_related('pedido__cliente', 'driver').prefetch_related('items__presentacion__producto', 'items__pedido_item__movimientos_inventario', 'items__pedido_item', 'notas_ajuste').get(id=delivery.invoice_id)
	return _invoice_pdf_response(invoice)
