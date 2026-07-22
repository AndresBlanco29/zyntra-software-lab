from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import transaction
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
import logging

from config.core.datetime_formats import format_local_date, format_local_datetime
from django.utils import timezone
from config.core.profit import attach_profit_to_order_item, summarize_order_profit
from config.core.product_ordering import order_pedido_items_for_display
from config.core.shipment_summary import build_shipment_summary_from_pedido_items, with_total_pallets
from config.core.pdf_branding import (
	BRAND_BORDER,
	BRAND_MUTED_TEXT,
	BRAND_PRIMARY,
	BRAND_SURFACE,
	BRAND_TEXT,
	build_pdf_brand_banner,
)
from config.core.workflow_badges import _safe_related, build_order_workflow_badge
from config.clientes.credit_limit import evaluate_customer_credit_limit, resolve_credit_limit_alert, unblock_credit_limit_blocked_order
from config.clientes.models import ClienteCreditoLimiteAlerta
from config.usuarios.permissions import internal_permission_required
from config.usuarios.models import Usuario
from config.inventario.services import ajustar_cantidad_item_pedido_despues_picking, ajustar_reserva_item_pedido, reemplazar_presentacion_item_pedido, reemplazar_presentacion_item_pedido_despues_picking

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.clientes.models import Cliente
from config.facturacion.models import Invoice, NotaAjuste
from config.facturacion.views import _build_invoice_pdf_shipment_summary_table
from config.facturacion.services import (
	DEFAULT_SUGGESTED_PROFIT_PERCENTAGE,
	build_customer_invoice_sale_price_options,
	get_recent_customer_invoice_items_by_presentation,
	resolve_presentacion_suggested_unit_price,
	summarize_pending_customer_notes,
)
from config.integrations.quickbooks.services import get_connection_status
from config.integrations.quickbooks.views import get_dashboard_sync_context
from config.notificaciones.models import Notificacion
from config.productos.models import Presentacion, ConfiguracionDescuentos, ConfiguracionPrecios
from config.inventario.models import StockPresentacion

from .models import Pedido, PedidoItem
from .crm_pipeline import build_crm_pipeline
from .dispatch_orders import build_dispatch_order_page, get_dispatch_order_counts
from .services import (
	actualizar_cantidad_linea_pedido_sin_aplicar_inventario,
	anular_pedido_desde_backoffice,
	asignar_picking_a_seleccionador,
	build_pedido_edit_lock_context,
	calcular_precio_unitario_neto_item,
	calcular_subtotal_item_pedido,
	crear_pedido_parcial,
	eliminar_linea_pedido_desde_backoffice,
	eliminar_pedido_desde_backoffice,
	ensure_pedido_edit_lock_owner,
	evaluar_stock_fisico_verificacion_picking,
	guardar_verificacion_picking,
	build_pedido_inventory_needs_analysis,
	normalizar_descuento_item_pedido,
	parse_lineas_parcial_desde_payload,
	pedido_puede_crear_parcial,
	puede_anular_pedido_desde_backoffice,
	puede_eliminar_pedido_desde_backoffice,
	recalcular_pedido,
	refresh_pedido_edit_lock,
	reemplazar_presentacion_linea_pedido_sin_aplicar_inventario,
	release_pedido_edit_lock,
	resolver_bloqueo_picking_desde_backoffice,
	resolver_nota_cliente_desde_backoffice,
	resolve_picking_send_ui_state,
	validar_estado_backoffice_con_bloqueo,
	notificar_cliente_pedido,
)


logger = logging.getLogger(__name__)

BACKOFFICE_PEDIDOS_PAGE_SIZE = 50


def _is_backoffice_user(user):
	return bool(user and user.is_authenticated and (user.is_superuser or user.role in {'admin', 'backoffice'}))


def _is_selector_user(user):
	return bool(user and user.is_authenticated and (user.is_superuser or user.role in {'admin', 'seleccionador'}))


def _item_has_picker_change_banner(item, pedido):
	if item.selector_added_by_picker or item.selector_changed_presentation:
		return True
	return pedido.estado == 'VERIFICADO_AJUSTADO' and item.selector_changed_quantity


def _parse_decimal(value, default='0'):
	text = str(value if value is not None else default).strip().replace(',', '.')
	if not text:
		text = str(default)
	try:
		return Decimal(text)
	except (InvalidOperation, ValueError, TypeError):
		return Decimal(str(default))


def _parse_quantity(value, default=1):
	try:
		quantity = int(value)
	except (TypeError, ValueError):
		quantity = default
	return max(quantity, 1)


def _parse_non_negative_quantity(value, default=0):
	try:
		quantity = int(value)
	except (TypeError, ValueError):
		quantity = default
	return max(quantity, 0)


def _validate_selector_line_reviews(
	request,
	*,
	pedido,
	posted_new_presentations,
	cantidades_reales,
	presentacion_updates,
):
	requires_full_review = not pedido.picking_verificado_en
	full_review_message = _(
		'Check every product line in the Reviewed column to confirm you verified the full picking list before saving.'
	)
	changed_review_message = _(
		'Check Reviewed only for each line you changed or added before saving.'
	)
	baseline_quantities = _saved_selector_picking_quantities(pedido)

	for item in pedido.items.all():
		if requires_full_review:
			needs_review = True
		else:
			baseline_qty = baseline_quantities.get(
				item.id,
				int(item.cantidad_inventario_aplicada or item.cantidad or 0),
			)
			posted_qty = int(cantidades_reales.get(item.id, baseline_qty))
			posted_presentation = int(presentacion_updates.get(item.id, item.presentacion_id))
			needs_review = posted_qty != baseline_qty or posted_presentation != int(item.presentacion_id)

		if needs_review and request.POST.get(f'linea_revisada_{item.id}') != 'on':
			raise ValidationError(full_review_message if requires_full_review else changed_review_message)

	additional_rows_with_product = sum(1 for presentacion_id in posted_new_presentations if (presentacion_id or '').strip())
	reviewed_additional_rows = len(request.POST.getlist('linea_revisada_adicional[]'))
	if reviewed_additional_rows < additional_rows_with_product:
		raise ValidationError(full_review_message if requires_full_review else changed_review_message)


def _quantize_money(value):
	return Decimal(str(value or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _calculate_margin_percentage(base_price, target_price):
	base_decimal = _parse_decimal(base_price, 0)
	target_decimal = _parse_decimal(target_price, 0)
	if target_decimal <= 0:
		return Decimal('0.00')
	percentage = (Decimal('1') - (base_decimal / target_decimal)) * Decimal('100')
	return percentage.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _pedido_item_customer_unit_price(item):
	case_price = _parse_decimal(item.precio, 0)
	units = getattr(item.presentacion, 'unidades', 0) or 0
	if units <= 0:
		return _quantize_money(case_price)
	return (case_price / Decimal(str(units))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _invoice_line_discount_percentage_for_item(item):
	if item.descuento_aplicado:
		discount_amount = _quantize_money(item.descuento_monto)
		list_price = _parse_decimal(item.precio, 0)
		if discount_amount > 0 and list_price > 0:
			return ((discount_amount / list_price) * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
	product_discount = max(int(getattr(item.presentacion.producto, 'descuento', 0) or 0), 0)
	return Decimal(str(product_discount))


def _invoice_line_net_unit_price(item):
	return calcular_precio_unitario_neto_item(
		precio=item.precio,
		descuento_aplicado=item.descuento_aplicado,
		descuento_monto=item.descuento_monto,
	)


def _build_invoice_suggested_price_row(item):
	net_unit_price = _invoice_line_net_unit_price(item)
	quantity = int(item.cantidad or 0)
	line_subtotal = calcular_subtotal_item_pedido(
		precio=item.precio,
		cantidad=quantity,
		descuento_aplicado=item.descuento_aplicado,
		descuento_monto=item.descuento_monto,
	)
	return {
		'item_id': item.id,
		'product_name': item.presentacion.producto.nombre,
		'presentation_name': item.presentacion.nombre_empaque_cliente,
		'quantity': quantity,
		'base_unit_value': format(_pedido_item_customer_unit_price(item), '.2f'),
		'list_unit_value': format(item.precio, '.2f'),
		'default_discount': format(_invoice_line_discount_percentage_for_item(item), '.2f'),
		'descuento_aplicado': bool(item.descuento_aplicado),
		'descuento_monto': format(_quantize_money(item.descuento_monto), '.2f'),
		'final_unit_value': format(net_unit_price, '.2f'),
		'line_subtotal_value': format(line_subtotal, '.2f'),
		'default_value': format(resolve_presentacion_suggested_unit_price(presentacion=item.presentacion, base_case_price=net_unit_price), '.2f'),
		'default_percentage': format(DEFAULT_SUGGESTED_PROFIT_PERCENTAGE, '.2f'),
	}


def _pedido_state_label(state):
	return {
		'RECIBIDO': _('Received'),
		'EN_GESTION': _('In progress'),
		'LISTO_PARA_PICKING': _('Ready for picking'),
		'PARA_VERIFICAR': _('Pending verification'),
		'VERIFICADO_AJUSTADO': _('Verified and adjusted'),
		'INVOICE_GENERADA': _('Invoice generated'),
		'DESPACHADO': _('Dispatched'),
		'CANCELADO': _('Cancelled'),
	}.get(state, state)


def _pedido_origin_label(origin):
	return {
		'CLIENTE': _('Customer'),
		'VENDEDOR': _('Sales'),
	}.get(origin, origin)


def _pedido_state_choices():
	return [(code, _pedido_state_label(code)) for code, _label in Pedido.ESTADO_CHOICES]


def _selector_pedidos_queryset(user):
	queryset = Pedido.objects.select_related(
		'cliente__usuario',
		'seleccionador',
		'invoice',
	).prefetch_related('items__presentacion__producto').order_by('-actualizada_en', '-creada_en')
	if user.is_superuser or getattr(user, 'role', '') == 'admin':
		return queryset.filter(seleccionador__isnull=False)
	return queryset.filter(seleccionador=user)


SELECTOR_PICKING_PROCESS_COMPLETED_STATES = frozenset({
	'INVOICE_GENERADA',
	'DESPACHADO',
	'CANCELADO',
})


def _selector_pedido_has_voided_invoice(pedido):
	invoice = _safe_related(pedido, 'invoice')
	return bool(invoice and invoice.estado == 'ANULADA')


def _selector_picking_process_completed(pedido):
	return (
		pedido.estado in SELECTOR_PICKING_PROCESS_COMPLETED_STATES
		or _selector_pedido_has_voided_invoice(pedido)
	)


def _selector_picking_done_rank_annotation():
	return Case(
		When(estado__in=list(SELECTOR_PICKING_PROCESS_COMPLETED_STATES), then=Value(1)),
		When(
			Exists(Invoice.objects.filter(pedido_id=OuterRef('pk'), estado='ANULADA')),
			then=Value(1),
		),
		default=Value(0),
		output_field=IntegerField(),
	)


def _filter_selector_picking_queryset(queryset, search_query):
	query_text = str(search_query or '').strip()
	if not query_text:
		return queryset
	filters = Q(cliente__nombre_empresa__icontains=query_text)
	if query_text.isdigit():
		filters |= Q(id=int(query_text))
	return queryset.filter(filters)


def _pedido_has_picking_draft(pedido):
	if pedido.picking_progress_saved_at:
		return True
	progress = pedido.picking_progress or {}
	return bool(progress)


def _annotate_selector_picking_rows(pedidos):
	for pedido in pedidos:
		invoice_voided = _selector_pedido_has_voided_invoice(pedido)
		pedido.picker_process_completed = _selector_picking_process_completed(pedido)
		pedido.has_picking_draft = _pedido_has_picking_draft(pedido) and not pedido.picker_process_completed
		if invoice_voided or pedido.estado == 'CANCELADO':
			pedido.estado_label = _('Cancelled')
		else:
			pedido.estado_label = _pedido_state_label(pedido.estado)
		pedido.workflow_badge = build_order_workflow_badge(pedido)
	return pedidos


def _build_selector_item_rows(pedido, actual_quantity_overrides=None, presentation_overrides=None):
	rows = []
	actual_quantity_overrides = actual_quantity_overrides or {}
	presentation_overrides = presentation_overrides or {}
	picking_verified = bool(pedido.picking_verificado_en)
	for item in pedido.items.select_related('presentacion__producto').all():
		product_presentations = list(
			item.presentacion.producto.presentaciones.select_related('stock_operativo').order_by('nombre')
		)
		if item.id in actual_quantity_overrides:
			actual_quantity = actual_quantity_overrides[item.id]
		elif picking_verified:
			actual_quantity = int(item.cantidad_inventario_aplicada or item.cantidad or 0)
		else:
			actual_quantity = 0
		selected_presentation_id = presentation_overrides.get(item.id, item.presentacion_id)
		selected_presentation = next(
			(presentation for presentation in product_presentations if presentation.id == selected_presentation_id),
			item.presentacion,
		)
		selected_stock = next(
			(
				option['stock_fisico']
				for option in [
					{
						'id': presentation.id,
						'stock_fisico': int(getattr(getattr(presentation, 'stock_operativo', None), 'stock_fisico', 0) or 0),
					}
					for presentation in product_presentations
				]
				if option['id'] == selected_presentation_id
			),
			int(getattr(getattr(item.presentacion, 'stock_operativo', None), 'stock_fisico', 0) or 0),
		)
		rows.append({
			'id': item.id,
			'product': item.presentacion.producto.nombre,
			'presentation': item.presentacion.nombre_empaque_cliente,
			'presentation_id': selected_presentation_id,
			'baseline_presentation_id': item.presentacion_id,
			'presentation_options': [
				{
					'id': presentation.id,
					'label': presentation.nombre_traducido,
					'stock_fisico': int(getattr(getattr(presentation, 'stock_operativo', None), 'stock_fisico', 0) or 0),
				}
				for presentation in product_presentations
			],
			'requested_quantity': item.cantidad_solicitada_documentada,
			'actual_quantity': actual_quantity,
			'baseline_quantity': (
				int(item.cantidad_inventario_aplicada or item.cantidad or 0) if picking_verified else 0
			),
			'stock_physical': selected_stock,
			'applied_quantity': int(item.cantidad_inventario_aplicada or 0),
			'case_weight': Decimal(str(selected_presentation.peso_por_caja or '0')),
		})
	rows.sort(key=lambda row: (-row['case_weight'], row['product'].casefold(), row['id']))
	return rows


def _saved_selector_picking_quantities(pedido):
	if not pedido.picking_verificado_en:
		return {}
	return {
		item.id: int(item.cantidad_inventario_aplicada or item.cantidad or 0)
		for item in pedido.items.all()
	}


@login_required
@internal_permission_required('backoffice.dashboard.view')
def backoffice_dashboard(request):
	dispatch_counts = get_dispatch_order_counts()
	context = {
		'ordenes_pendientes': dispatch_counts['pending_count'],
		'solicitudes_pendientes': dispatch_counts['pending_requests_count'],
		'ordenes_recibidas': dispatch_counts['pending_dispatch_count'],
		'ordenes_en_gestion': Pedido.objects.filter(estado='EN_GESTION').count(),
		'ordenes_listas_picking': Pedido.objects.filter(estado='LISTO_PARA_PICKING').count(),
		'inventario_agotado': StockPresentacion.objects.filter(stock_disponible__lte=0).count(),
		'pending_adjustment_notes_count': NotaAjuste.objects.filter(estado='BORRADOR').count(),
		'unread_adjustment_notifications_count': Notificacion.objects.filter(tipo='NOTA_AJUSTE', leida=False).count(),
		'notificaciones': Notificacion.objects.filter(tipo__in=('PEDIDO', 'COTIZACION')).order_by('-creada_en')[:8],
		'quickbooks_status': get_connection_status(),
	}
	context.update(get_dashboard_sync_context(request=request))
	return render(request, 'backoffice/dashboard.html', context)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_pedidos(request):
	search_query = (request.GET.get('q') or '').strip()
	sort_dir = (request.GET.get('sort') or 'desc').strip().lower()
	if sort_dir not in {'asc', 'desc'}:
		sort_dir = 'desc'
	date_from = (request.GET.get('date_from') or '').strip()
	date_to = (request.GET.get('date_to') or '').strip()
	view_mode, page_obj = build_dispatch_order_page(
		view_mode=request.GET.get('view'),
		page_number=request.GET.get('page'),
		page_size=BACKOFFICE_PEDIDOS_PAGE_SIZE,
		search_term=search_query,
		sort_dir=sort_dir,
		date_from=date_from,
		date_to=date_to,
	)
	counts = get_dispatch_order_counts()
	return render(request, 'backoffice/pedidos_lista.html', {
		'dispatch_orders': page_obj,
		'page_obj': page_obj,
		'view_mode': view_mode,
		'search_query': search_query,
		'sort_dir': sort_dir,
		'date_from': date_from,
		'date_to': date_to,
		**counts,
	})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_crm_pipeline(request):
	period = (request.GET.get('period') or 'today').strip()
	if period not in {'today', 'week', 'month'}:
		period = 'today'

	vendedor_id = request.GET.get('vendedor') or None
	cliente_id = request.GET.get('cliente') or None
	try:
		vendedor_id = int(vendedor_id) if vendedor_id else None
	except (TypeError, ValueError):
		vendedor_id = None
	try:
		cliente_id = int(cliente_id) if cliente_id else None
	except (TypeError, ValueError):
		cliente_id = None

	pipeline = build_crm_pipeline(
		period=period,
		search_term=request.GET.get('q') or '',
		vendedor_id=vendedor_id,
		cliente_id=cliente_id,
	)
	vendedores = (
		Usuario.objects.filter(role='vendedor', is_active=True)
		.order_by('first_name', 'last_name', 'username')
	)
	clientes = (
		Cliente.objects.filter(pedidos__isnull=False)
		.distinct()
		.order_by('nombre_empresa')
	)
	return render(request, 'backoffice/crm_pipeline.html', {
		**pipeline,
		'vendedores': vendedores,
		'clientes': clientes,
	})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_pedido_detalle(request, pedido_id):
	pedido = (
		Pedido.objects.select_related(
			'cliente__usuario',
			'vendedor',
			'seleccionador',
			'invoice',
			'invoice__driver',
			'invoice__delivery',
		)
		.prefetch_related(
			'items__presentacion__producto',
			'items__presentacion__stock_operativo',
			'items__selector_original_presentacion',
		)
		.filter(id=pedido_id)
		.first()
	)
	if pedido is None:
		from config.cotizaciones.models import Cotizacion

		cotizacion = Cotizacion.objects.filter(id=pedido_id, pedido_generado__isnull=True).first()
		if cotizacion is not None:
			messages.info(
				request,
				_('Opening quote #%(id)s. That reference belongs to a quotation, not a sales order.') % {
					'id': cotizacion.id,
				},
			)
			return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)
		messages.error(request, _('Sales order #%(id)s was not found.') % {'id': pedido_id})
		return redirect('backoffice_pedidos')

	pedido_items = list(pedido.items.select_related('presentacion__producto', 'presentacion__stock_operativo'))
	if not hasattr(pedido, 'invoice'):
		from config.productos.promotions import asegurar_promociones_en_pedido
		if asegurar_promociones_en_pedido(pedido):
			pedido.refresh_from_db(fields=['total'])
			pedido_items = list(pedido.items.select_related('presentacion__producto', 'presentacion__stock_operativo'))
	pedido.workflow_badge = build_order_workflow_badge(pedido)
	picker_stock_evaluation = evaluar_stock_fisico_verificacion_picking(
		pedido_items=pedido_items,
		cantidades_reales={item.id: item.cantidad for item in pedido_items},
	)
	picker_stock_shortage_rows = [
		{
			'product_name': item.presentacion.producto.nombre,
			'presentation_name': item.presentacion.nombre_empaque_cliente,
			'quantity_to_pick': picker_stock_evaluation[item.id]['cantidad_pendiente_aplicar'],
			'available_physical_stock': picker_stock_evaluation[item.id]['available_packages'],
			'shortage_amount': picker_stock_evaluation[item.id]['shortage_amount'],
		}
		for item in pedido_items
		if picker_stock_evaluation[item.id]['has_shortage']
	]
	for item in pedido_items:
		item.presentation_options = list(item.presentacion.producto.presentaciones.order_by('nombre'))
		item_evaluation = picker_stock_evaluation[item.id]
		item.has_picker_stock_shortage = (
			pedido.picking_bloqueado
			and item_evaluation['has_shortage']
			and int(item.cantidad or 0) > 0
		)
		item.stock_fisico_packages = item_evaluation['stock_fisico']
	_enrich_pedido_items_with_price_options(pedido=pedido, pedido_items=pedido_items)
	inventory_needs_analysis = build_pedido_inventory_needs_analysis(pedido=pedido, pedido_items=pedido_items)

	if request.method == 'POST':
		if not request.user.has_internal_permission('backoffice.orders.manage'):
			return redirect('backoffice_pedidos')
		estado_anterior = pedido.estado
		nota_anterior = pedido.nota_backoffice or ''
		before_items = [
			{
				'item_id': item.id,
				'producto': item.presentacion.producto.nombre,
				'presentacion': item.presentacion.nombre_empaque_cliente,
				'cantidad': item.cantidad,
				'precio': str(item.precio),
				'descuento_aplicado': bool(item.descuento_aplicado),
				'descuento_monto': str(item.descuento_monto),
				'subtotal': str(item.subtotal),
			}
			for item in pedido.items.select_related('presentacion__producto')
		]
		total_anterior = str(pedido.total)
		try:
			ensure_pedido_edit_lock_owner(pedido=pedido, user=request.user)
			with transaction.atomic():
				if hasattr(pedido, 'invoice'):
					raise ValidationError(_('Orders with a generated invoice are locked on this screen.'))
				nuevo_estado = request.POST.get('estado') or pedido.estado
				validar_estado_backoffice_con_bloqueo(pedido, nuevo_estado)

				pedido.estado = nuevo_estado
				pedido.nota_backoffice = (request.POST.get('nota_backoffice') or '').strip()
				pedido.save(update_fields=['estado', 'nota_backoffice', 'actualizada_en'])

				for item in list(pedido.items.select_related('presentacion__producto')):
					if request.POST.get(f'eliminar_{item.id}'):
						eliminar_linea_pedido_desde_backoffice(item=item, creado_por=request.user)
						continue

					nueva_presentacion_id = request.POST.get(f'presentacion_{item.id}')
					if nueva_presentacion_id and str(item.presentacion_id) != str(nueva_presentacion_id):
						nueva_presentacion = get_object_or_404(Presentacion.objects.select_related('producto'), id=nueva_presentacion_id)
						if item.cantidad_inventario_aplicada:
							item = reemplazar_presentacion_item_pedido_despues_picking(item=item, nueva_presentacion=nueva_presentacion, creado_por=request.user)
						elif int(item.cantidad_reservada_inventario or 0) > 0:
							item = reemplazar_presentacion_item_pedido(item=item, nueva_presentacion=nueva_presentacion, creado_por=request.user)
						else:
							item = reemplazar_presentacion_linea_pedido_sin_aplicar_inventario(item=item, nueva_presentacion=nueva_presentacion)

					nueva_cantidad = _parse_non_negative_quantity(request.POST.get(f'cantidad_{item.id}'), item.cantidad)
					if item.cantidad_inventario_aplicada:
						item = ajustar_cantidad_item_pedido_despues_picking(item=item, nueva_cantidad=nueva_cantidad, creado_por=request.user)
					elif int(item.cantidad_reservada_inventario or 0) > 0:
						item = ajustar_reserva_item_pedido(item=item, nueva_cantidad=nueva_cantidad, creado_por=request.user)
					else:
						item = actualizar_cantidad_linea_pedido_sin_aplicar_inventario(item=item, nueva_cantidad=nueva_cantidad)
					item.precio = _parse_decimal(request.POST.get(f'precio_{item.id}'), item.precio)
					item.descuento_aplicado, item.descuento_monto = normalizar_descuento_item_pedido(
						precio=item.precio,
						descuento_aplicado=request.POST.get(f'descuento_aplicado_{item.id}'),
						descuento_monto=_parse_decimal(request.POST.get(f'descuento_monto_{item.id}'), item.descuento_monto),
					)
					item.subtotal = calcular_subtotal_item_pedido(
						precio=item.precio,
						cantidad=item.cantidad,
						descuento_aplicado=item.descuento_aplicado,
						descuento_monto=item.descuento_monto,
					)
					item.save(update_fields=['precio', 'descuento_aplicado', 'descuento_monto', 'subtotal'])

				nueva_presentacion_ids = [value.strip() for value in request.POST.getlist('presentacion_nueva[]') if str(value or '').strip()]
				nueva_cantidades = request.POST.getlist('cantidad_nueva[]')
				nueva_precios = request.POST.getlist('precio_nuevo[]')
				# Backward compatibility with the previous single-line add fields.
				if not nueva_presentacion_ids and request.POST.get('presentacion_nueva'):
					nueva_presentacion_ids = [str(request.POST.get('presentacion_nueva')).strip()]
					nueva_cantidades = [request.POST.get('cantidad_nueva') or '1']
					nueva_precios = [request.POST.get('precio_nuevo') or '0']

				for index, nueva_presentacion_id in enumerate(nueva_presentacion_ids):
					presentacion = get_object_or_404(Presentacion.objects.select_related('producto'), id=nueva_presentacion_id)
					cantidad_value = nueva_cantidades[index] if index < len(nueva_cantidades) else '1'
					precio_value = nueva_precios[index] if index < len(nueva_precios) else '0'
					cantidad_nueva = _parse_quantity(cantidad_value, 1)
					precio_nuevo = _parse_decimal(precio_value, 0)
					descuento_aplicado_nuevo, descuento_monto_nuevo = normalizar_descuento_item_pedido(
						precio=precio_nuevo,
						descuento_aplicado=False,
						descuento_monto=Decimal('0'),
					)
					PedidoItem.objects.create(
						pedido=pedido,
						presentacion=presentacion,
						cantidad_solicitada=cantidad_nueva,
						cantidad=cantidad_nueva,
						precio=precio_nuevo,
						descuento_aplicado=descuento_aplicado_nuevo,
						descuento_monto=descuento_monto_nuevo,
						subtotal=calcular_subtotal_item_pedido(
							precio=precio_nuevo,
							cantidad=cantidad_nueva,
							descuento_aplicado=descuento_aplicado_nuevo,
							descuento_monto=descuento_monto_nuevo,
						),
					)

				recalcular_pedido(pedido)
		except ValidationError as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
			return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

		release_pedido_edit_lock(pedido=pedido, user=request.user)
		from config.auditoria.business_events import log_business_event
		from config.auditoria.enrichment import build_line_item_changes
		from config.auditoria.models import AuditLog
		pedido.refresh_from_db()
		line_items = [
			{
				'item_id': item.id,
				'producto': item.presentacion.producto.nombre,
				'presentacion': item.presentacion.nombre_empaque_cliente,
				'cantidad': item.cantidad,
				'precio': str(item.precio),
				'descuento_aplicado': bool(item.descuento_aplicado),
				'descuento_monto': str(item.descuento_monto),
				'subtotal': str(item.subtotal),
			}
			for item in pedido.items.select_related('presentacion__producto')
		]
		changes = []
		if estado_anterior != pedido.estado:
			changes.append({'field': 'Status', 'before': estado_anterior, 'after': pedido.estado})
		if (nota_anterior or '') != (pedido.nota_backoffice or ''):
			changes.append({'field': 'Backoffice note', 'before': nota_anterior, 'after': pedido.nota_backoffice})
		if total_anterior != str(pedido.total):
			changes.append({'field': 'Total', 'before': total_anterior, 'after': str(pedido.total)})
		changes.extend(build_line_item_changes(before_items, line_items))
		log_business_event(
			request.user,
			action_label=_('Updated sales order #%(id)s') % {'id': pedido.id},
			action_category=AuditLog.CATEGORY_UPDATE,
			entity_type='Pedido',
			entity_id=str(pedido.id),
			entity_label=_('Order #%(id)s - %(client)s') % {'id': pedido.id, 'client': pedido.cliente.nombre_empresa},
			metadata={
				'estado_anterior': estado_anterior,
				'estado_nuevo': pedido.estado,
				'nota_backoffice': pedido.nota_backoffice,
				'total': str(pedido.total),
				'line_items': line_items,
				'line_items_before': before_items,
			},
			changes=changes,
			request=request,
			module='Orders',
		)
		messages.success(request, _('Sales order updated successfully.'))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	edit_lock_context = build_pedido_edit_lock_context(pedido=pedido, user=request.user)
	pedido_form_disabled = (
		edit_lock_context['pedido_edit_blocked']
		or hasattr(pedido, 'invoice')
	)
	can_manage_pedido = (
		request.user.has_internal_permission('backoffice.orders.manage')
		and not edit_lock_context['pedido_edit_blocked']
		and not pedido_form_disabled
	)
	can_send_picking, picking_send_button_label = resolve_picking_send_ui_state(pedido)
	pedido_raiz = pedido.pedido_raiz_efectivo
	parciales_relacionadas = list(
		Pedido.objects.filter(pedido_raiz=pedido_raiz)
		.order_by('indice_parcial', 'id')
	)

	context = {
		'pending_customer_notes_summary': summarize_pending_customer_notes(cliente=pedido.cliente),
		'pedido': pedido,
		'pedido_items': pedido_items,
		'pedido_has_picker_changes': any(_item_has_picker_change_banner(item, pedido) for item in pedido_items),
		'invoice': getattr(pedido, 'invoice', None),
		'picker_stock_shortage_blocked': bool(pedido.picking_bloqueado and picker_stock_shortage_rows),
		'picker_stock_shortage_rows': picker_stock_shortage_rows,
		'inventory_needs_analysis': inventory_needs_analysis,
		'pedido_estado_label': _pedido_state_label(pedido.estado),
		'pedido_origen_label': _pedido_origin_label(pedido.origen),
		'state_choices': _pedido_state_choices(),
		'drivers': Usuario.objects.filter(role='driver', is_active=True).order_by('first_name', 'last_name', 'username'),
		'selectores': Usuario.objects.filter(role='seleccionador', is_active=True).order_by('first_name', 'last_name', 'username'),
		'lineas_bloqueadas_para_picking': hasattr(pedido, 'invoice'),
		'pedido_form_disabled': pedido_form_disabled,
		'can_manage_pedido': can_manage_pedido,
		'can_send_picking': can_send_picking,
		'picking_send_button_label': picking_send_button_label,
		'can_create_partial_order': can_manage_pedido and pedido_puede_crear_parcial(pedido),
		'pedido_raiz': pedido_raiz if pedido.es_parcial else None,
		'parciales_relacionadas': parciales_relacionadas,
		'can_unlock_pedido': (
			pedido.picking_bloqueado
			and pedido.estado == 'VERIFICADO_AJUSTADO'
			and not hasattr(pedido, 'invoice')
		),
		'can_resolve_nota_cliente': (
			pedido.tiene_nota_cliente_pendiente
			and not hasattr(pedido, 'invoice')
			and can_manage_pedido
		),
		'can_void_pedido': puede_anular_pedido_desde_backoffice(pedido) and can_manage_pedido,
		'can_delete_pedido': puede_eliminar_pedido_desde_backoffice(pedido) and can_manage_pedido,
		'can_send_customer_order_email': request.user.has_internal_permission('backoffice.orders.manage'),
		'cliente_tiene_email': bool(
			(getattr(getattr(pedido.cliente, 'usuario', None), 'email', '') or '').strip()
		),
		'invoice_suggested_price_rows': [
			_build_invoice_suggested_price_row(item)
			for item in pedido.items.select_related('presentacion__producto')
			if item.cantidad > 0
		],
		'bulk_price_options': _build_bulk_pedido_price_options(),
		'discount_preset_options': _build_pedido_discount_preset_options(),
		'presentation_price_map': _build_pedido_presentation_price_map(pedido=pedido, pedido_items=pedido_items),
		'can_view_product_cost': _is_backoffice_user(request.user),
		'presentation_cost_map': (
			_build_pedido_presentation_cost_map(pedido_items=pedido_items)
			if _is_backoffice_user(request.user)
			else {}
		),
		'default_price_key': _default_presentacion_price_key_for_pedido(pedido=pedido),
		'order_profit_summary': summarize_order_profit(pedido_items),
		'credit_limit_evaluation': evaluate_customer_credit_limit(cliente=pedido.cliente, additional_amount=pedido.total),
		'pending_credit_limit_alert': (
			ClienteCreditoLimiteAlerta.objects.filter(
				pedido=pedido,
				estado=ClienteCreditoLimiteAlerta.ESTADO_PENDIENTE,
			).order_by('-creado_en').first()
		),
		'blocked_credit_limit_alert': (
			ClienteCreditoLimiteAlerta.objects.filter(
				pedido=pedido,
				estado=ClienteCreditoLimiteAlerta.ESTADO_BLOQUEADO,
			).order_by('-creado_en').first()
			if pedido.credit_limit_bloqueado
			else None
		),
		'show_credit_limit_alert_modal': request.GET.get('credit_limit_alert') == '1',
		**edit_lock_context,
	}
	return render(request, 'backoffice/pedido_detalle.html', context)


def _extract_partial_lineas_from_post(request, pedido):
	lineas = []
	for item in pedido.items.all():
		selected = request.POST.get(f'select_{item.id}')
		if not selected:
			continue
		raw_qty = request.POST.get(f'qty_{item.id}')
		try:
			cantidad = int(raw_qty)
		except (TypeError, ValueError):
			cantidad = 0
		lineas.append({'item_id': item.id, 'cantidad': cantidad})
	return lineas


def _build_partial_preview_rows(*, pedido, lineas):
	selected_map = {item.id: qty for item, qty in lineas}
	partial_rows = []
	remaining_rows = []
	for item in pedido.items.select_related('presentacion__producto'):
		pending = int(item.cantidad or 0)
		partial_qty = int(selected_map.get(item.id, 0) or 0)
		if partial_qty > 0:
			partial_rows.append({
				'item': item,
				'cantidad': partial_qty,
			})
		remaining_qty = pending - partial_qty
		if remaining_qty > 0:
			remaining_rows.append({
				'item': item,
				'cantidad': remaining_qty,
			})
	return partial_rows, remaining_rows


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_pedido_partial(request, pedido_id):
	pedido = get_object_or_404(
		Pedido.objects.select_related('cliente', 'vendedor').prefetch_related('items__presentacion__producto'),
		id=pedido_id,
	)
	if not pedido_puede_crear_parcial(pedido):
		messages.error(request, _('This sales order cannot be split into a partial order.'))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	pedido_items = [
		item
		for item in order_pedido_items_for_display(pedido)
		if int(item.cantidad or 0) > 0
	]
	selected_map = {}
	form_error = None

	if request.method == 'POST':
		lineas_payload = _extract_partial_lineas_from_post(request, pedido)
		selected_map = {row['item_id']: row['cantidad'] for row in lineas_payload}
		if request.POST.get('action') != 'edit':
			try:
				lineas = parse_lineas_parcial_desde_payload(pedido=pedido, lineas_payload=lineas_payload)
				preview_rows, remaining_rows = _build_partial_preview_rows(pedido=pedido, lineas=lineas)
				return render(request, 'backoffice/pedido_parcial_confirmar.html', {
					'pedido': pedido,
					'partial_rows': preview_rows,
					'remaining_rows': remaining_rows,
					'lineas_payload': lineas_payload,
				})
			except ValidationError as exc:
				form_error = exc.messages[0] if getattr(exc, 'messages', None) else str(exc)
				messages.error(request, form_error)

	for item in pedido_items:
		item.partial_selected = item.id in selected_map
		item.partial_qty = selected_map.get(item.id, item.cantidad)

	return render(request, 'backoffice/pedido_parcial_crear.html', {
		'pedido': pedido,
		'pedido_items': pedido_items,
		'form_error': form_error,
	})


@login_required
@internal_permission_required('backoffice.orders.manage')
@require_POST
def backoffice_pedido_partial_confirm(request, pedido_id):
	pedido = get_object_or_404(
		Pedido.objects.select_related('cliente', 'vendedor').prefetch_related('items__presentacion__producto'),
		id=pedido_id,
	)
	if request.POST.get('action') == 'back':
		# Re-open selection with the same posted quantities.
		pedido_items = [
			item
			for item in order_pedido_items_for_display(pedido)
			if int(item.cantidad or 0) > 0
		]
		lineas_payload = _extract_partial_lineas_from_post(request, pedido)
		selected_map = {row['item_id']: row['cantidad'] for row in lineas_payload}
		for item in pedido_items:
			item.partial_selected = item.id in selected_map
			item.partial_qty = selected_map.get(item.id, item.cantidad)
		return render(request, 'backoffice/pedido_parcial_crear.html', {
			'pedido': pedido,
			'pedido_items': pedido_items,
			'form_error': None,
		})

	try:
		lineas_payload = _extract_partial_lineas_from_post(request, pedido)
		parcial = crear_pedido_parcial(
			pedido=pedido,
			lineas_payload=lineas_payload,
			usuario=request.user,
			request=request,
		)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('backoffice_pedido_partial', pedido_id=pedido.id)

	messages.success(
		request,
		_('Partial order #%(numero)s was created successfully.') % {'numero': parcial.numero_display},
	)
	return redirect('backoffice_pedido_detalle', pedido_id=parcial.id)


def _default_presentacion_price_for_pedido(*, presentacion, pedido):
	cliente = getattr(pedido, 'cliente', None)
	tier = cliente.get_nivel_precio_normalizado() if cliente and hasattr(cliente, 'get_nivel_precio_normalizado') else None
	price = presentacion.get_price_for_tier(tier)
	if price is None:
		price = presentacion.precio_1
	return _quantize_money(price or 0)


def _default_presentacion_price_key_for_pedido(*, pedido):
	return 'invoice_sale_1'


def _default_catalog_presentacion_price_key_for_pedido(*, pedido, presentacion):
	default_price = _default_presentacion_price_for_pedido(presentacion=presentacion, pedido=pedido)
	catalog_prices, _, _ = _build_catalog_presentacion_price_options(presentacion=presentacion, pedido=None)
	return _match_presentacion_price_key(catalog_prices, default_price) or 'precio_1'


def _build_catalog_presentacion_price_options(*, presentacion, pedido=None):
	prices = []
	for index in range(1, 6):
		key = f'precio_{index}'
		value = _quantize_money(getattr(presentacion, key, 0) or 0)
		prices.append({
			'key': key,
			'value': format(value, '.2f'),
			'label': f'{_("Price")} {index} - ${format(value, ".2f")}',
		})

	if getattr(presentacion, 'qb_price', None) is not None:
		qb_value = _quantize_money(presentacion.qb_price)
		prices.append({
			'key': 'qb_price',
			'value': format(qb_value, '.2f'),
			'label': f'QB-PRICE - ${format(qb_value, ".2f")}',
		})

	if pedido is not None:
		default_price = _default_presentacion_price_for_pedido(presentacion=presentacion, pedido=pedido)
		default_key = _default_catalog_presentacion_price_key_for_pedido(pedido=pedido, presentacion=presentacion)
	else:
		default_key = 'precio_1'
		default_price = _quantize_money(presentacion.precio_1 or 0)

	return prices, default_key, format(default_price, '.2f')


def _build_presentacion_price_options(*, presentacion, pedido=None):
	cliente = getattr(pedido, 'cliente', None) if pedido is not None else None
	prices = []

	if cliente is not None:
		prices.extend(build_customer_invoice_sale_price_options(cliente=cliente, presentacion=presentacion, limit=2))

	for index in range(1, 6):
		key = f'precio_{index}'
		value = _quantize_money(getattr(presentacion, key, 0) or 0)
		prices.append({
			'key': key,
			'value': format(value, '.2f'),
			'label': f'{_("Price")} {index} - ${format(value, ".2f")}',
		})

	if getattr(presentacion, 'qb_price', None) is not None:
		qb_value = _quantize_money(presentacion.qb_price)
		prices.append({
			'key': 'qb_price',
			'value': format(qb_value, '.2f'),
			'label': f'QB-PRICE - ${format(qb_value, ".2f")}',
		})

	if prices:
		if cliente is not None and prices[0]['key'].startswith('invoice_sale'):
			return prices, prices[0]['key'], prices[0]['value']
		default_price = (
			_default_presentacion_price_for_pedido(presentacion=presentacion, pedido=pedido)
			if pedido is not None
			else _quantize_money(presentacion.precio_1 or 0)
		)
		default_key = _match_presentacion_price_key(prices, default_price) or 'precio_1'
		return prices, default_key, format(default_price, '.2f')

	default_price = (
		_default_presentacion_price_for_pedido(presentacion=presentacion, pedido=pedido)
		if pedido is not None
		else _quantize_money(presentacion.precio_1 or 0)
	)
	return [], '', format(default_price, '.2f')


def _match_presentacion_price_key(price_options, current_price):
	current = format(_quantize_money(current_price or 0), '.2f')
	for option in price_options:
		if option['value'] == current:
			return option['key']
	return ''


def _build_bulk_pedido_price_options():
	options = [
		{
			'key': 'invoice_sale_1',
			'label': _('Most recent sale price'),
		},
		{
			'key': 'invoice_sale_2',
			'label': _('Second most recent sale price'),
		},
	]
	for index, margin in enumerate(ConfiguracionPrecios.obtener().porcentajes_lista(), start=1):
		options.append({
			'key': f'precio_{index}',
			'label': _('Price %(number)s (%(percentage)s%%)') % {
				'number': index,
				'percentage': margin,
			},
		})
	options.append({
		'key': 'qb_price',
		'label': 'QB-PRICE',
	})
	return options


def _build_pedido_discount_preset_options():
	return ConfiguracionDescuentos.obtener().opciones_activas()


def _match_discount_preset_key(discount_options, current_amount):
	current = format(_quantize_money(current_amount or 0), '.2f')
	for option in discount_options:
		if option['value'] == current:
			return option['key']
	return ''


def _build_pedido_presentation_price_map(*, pedido, pedido_items):
	presentation_ids = set()
	for item in pedido_items:
		for presentation in item.presentation_options:
			presentation_ids.add(presentation.id)

	price_map = {}
	for presentation in Presentacion.objects.filter(id__in=presentation_ids):
		options, _, _ = _build_presentacion_price_options(presentacion=presentation, pedido=pedido)
		price_map[str(presentation.id)] = options
	return price_map


def _build_pedido_presentation_cost_map(*, pedido_items):
	presentation_ids = set()
	for item in pedido_items:
		for presentation in item.presentation_options:
			presentation_ids.add(presentation.id)

	cost_map = {}
	for presentation in Presentacion.objects.filter(id__in=presentation_ids).only('id', 'costo'):
		cost_map[str(presentation.id)] = (
			format(presentation.costo, '.2f') if presentation.costo is not None else None
		)
	return cost_map


def _enrich_pedido_items_with_price_options(*, pedido, pedido_items):
	discount_options = _build_pedido_discount_preset_options()
	history_by_presentation = {}
	if pedido.cliente_id:
		history_by_presentation = get_recent_customer_invoice_items_by_presentation(
			cliente=pedido.cliente,
			presentation_ids=[item.presentacion_id for item in pedido_items],
			limit=2,
		)

	for item in pedido_items:
		price_options, _, _ = _build_presentacion_price_options(presentacion=item.presentacion, pedido=pedido)
		item.price_options = price_options
		item.recent_customer_sales = history_by_presentation.get(item.presentacion_id, [])
		item.selected_price_key = _match_presentacion_price_key(price_options, item.precio)
		item.discount_preset_options = discount_options
		item.selected_discount_preset_key = (
			_match_discount_preset_key(discount_options, item.descuento_monto)
			if item.descuento_aplicado
			else ''
		)
		attach_profit_to_order_item(item)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_buscar_presentaciones(request):
	query = (request.GET.get('q') or '').strip()
	if len(query) < 2:
		return JsonResponse({'results': []})

	presentaciones = (
		Presentacion.objects.select_related('producto')
		.filter(producto__activo=True)
		.filter(
			Q(producto__nombre__icontains=query)
			| Q(producto__nombre_en__icontains=query)
			| Q(nombre__icontains=query)
			| Q(producto__codigo_barras__icontains=query)
		)
		.order_by('producto__nombre', 'nombre')[:30]
	)

	pedido = None
	pedido_id = (request.GET.get('pedido_id') or '').strip()
	cotizacion_id = (request.GET.get('cotizacion_id') or '').strip()
	cliente_id = (request.GET.get('cliente_id') or '').strip()
	if pedido_id.isdigit():
		pedido = Pedido.objects.select_related('cliente').filter(id=int(pedido_id)).first()
	elif cotizacion_id.isdigit():
		from config.cotizaciones.models import Cotizacion
		pedido = Cotizacion.objects.select_related('cliente').filter(id=int(cotizacion_id)).first()
	elif cliente_id.isdigit():
		cliente = Cliente.objects.filter(id=int(cliente_id), aprobado=True).first()
		if cliente is not None:
			pedido = type('PriceContext', (), {'cliente': cliente})()

	results = []
	for presentacion in presentaciones:
		price_options, default_price_key, default_price = _build_catalog_presentacion_price_options(
			presentacion=presentacion,
			pedido=pedido,
		)
		result = {
			'id': presentacion.id,
			'label': f'{presentacion.producto.nombre} - {presentacion.nombre_empaque_cliente}',
			'price': default_price,
			'default_price_key': default_price_key,
			'prices': price_options,
		}
		if _is_backoffice_user(request.user):
			result['cost'] = (
				format(presentacion.costo, '.2f') if presentacion.costo is not None else None
			)
		results.append(result)

	return JsonResponse({'results': results})


@login_required
@require_POST
@internal_permission_required('backoffice.orders.manage')
def backoffice_enviar_pedido_cliente(request, pedido_id):
	pedido = get_object_or_404(
		Pedido.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'),
		id=pedido_id,
	)

	if not pedido.items.exists():
		messages.error(request, _('The sales order has no products to send to the customer.'))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	include_prices = request.POST.get('enviar_correo_con_precios', '0') == '1'
	cliente_tiene_email = bool(
		(getattr(getattr(pedido.cliente, 'usuario', None), 'email', '') or '').strip()
	)

	if not cliente_tiene_email:
		messages.warning(
			request,
			_('This customer does not have an email on file. Email notifications cannot be sent until an email is added to the customer profile.'),
		)
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	try:
		enviado = notificar_cliente_pedido(pedido, include_prices=include_prices)
	except Exception as exc:
		logger.exception('Error enviando cotizacion/email del pedido %s al cliente: %s', pedido.id, exc)
		messages.error(request, _('The customer email could not be sent.'))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	if enviado:
		if include_prices:
			messages.success(
				request,
				_('Quotation email for sales order #%(id)s was sent to the customer with prices.') % {
					'id': pedido.id,
				},
			)
		else:
			messages.success(
				request,
				_('Quotation email for sales order #%(id)s was sent to the customer without prices.') % {
					'id': pedido.id,
				},
			)
	else:
		messages.warning(request, _('The customer email could not be sent.'))

	return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_pedido_void(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	try:
		ensure_pedido_edit_lock_owner(pedido=pedido, user=request.user)
		anular_pedido_desde_backoffice(pedido=pedido, usuario=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		release_pedido_edit_lock(pedido=pedido, user=request.user)
		messages.success(request, _('Sales order voided successfully. Inventory was not changed.'))
	return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_pedido_delete(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	try:
		ensure_pedido_edit_lock_owner(pedido=pedido, user=request.user)
		eliminar_pedido_desde_backoffice(pedido=pedido)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	messages.success(request, _('Sales order deleted permanently. Inventory was not changed.'))
	return redirect('backoffice_pedidos')


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_asignar_picking(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente__usuario', 'seleccionador'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	selector_id = request.POST.get('seleccionador_id')
	seleccionador = get_object_or_404(Usuario, id=selector_id, role='seleccionador', is_active=True)

	try:
		ensure_pedido_edit_lock_owner(pedido=pedido, user=request.user)
		asignar_picking_a_seleccionador(pedido=pedido, seleccionador=seleccionador, asignado_por=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		release_pedido_edit_lock(pedido=pedido, user=request.user)
		messages.success(request, _('Picking ticket sent to selector successfully.'))

	return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_resolver_bloqueo_picking(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('invoice'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	try:
		ensure_pedido_edit_lock_owner(pedido=pedido, user=request.user)
		resolver_bloqueo_picking_desde_backoffice(pedido=pedido, usuario=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		release_pedido_edit_lock(pedido=pedido, user=request.user)
		messages.success(request, _('Order unlocked successfully. You can now generate the invoice.'))

	return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_resolver_nota_cliente(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('invoice'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	try:
		ensure_pedido_edit_lock_owner(pedido=pedido, user=request.user)
		resolver_nota_cliente_desde_backoffice(pedido=pedido, usuario=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		release_pedido_edit_lock(pedido=pedido, user=request.user)
		messages.success(request, _('Order comment resolved. You can continue with this sales order.'))

	return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_resolve_credit_limit(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	action = (request.POST.get('action') or '').strip().lower()
	comentario = (request.POST.get('comentario') or '').strip()
	autorizado_por = (request.POST.get('autorizado_por') or '').strip()
	try:
		with transaction.atomic():
			if action == 'unblock':
				unblock_credit_limit_blocked_order(
					pedido=pedido,
					usuario=request.user,
					comentario=comentario,
					autorizado_por=autorizado_por,
				)
			else:
				resolve_credit_limit_alert(
					pedido=pedido,
					usuario=request.user,
					action=action,
					comentario=comentario,
				)
	except ValueError as exc:
		if str(exc) == 'order_not_credit_blocked':
			messages.error(request, _('This order is not blocked by credit limit.'))
		elif str(exc) == 'unblock_authorized_by_required':
			messages.error(
				request,
				_('Enter the name of the person authorizing the unblock before continuing.'),
			)
		else:
			messages.error(request, _('The credit limit alert is no longer pending review.'))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	if action == 'release' or action == 'unblock':
		messages.success(
			request,
			_('Credit hold released for this order. Processing can continue.'),
		)
	elif action == 'block':
		messages.error(
			request,
			_('This order was blocked and the customer was placed on credit hold. The invoice cannot be issued.'),
		)
	return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_pedido_edit_lock_ping(request, pedido_id):
	if request.method != 'POST':
		return JsonResponse({'ok': False}, status=405)

	pedido = Pedido.objects.filter(id=pedido_id).first()
	if pedido is None:
		return JsonResponse({'ok': True})

	try:
		refresh_pedido_edit_lock(pedido=pedido, user=request.user)
	except ValidationError as exc:
		message = exc.messages[0] if getattr(exc, 'messages', None) else str(exc)
		return JsonResponse({'ok': False, 'error': message}, status=409)

	return JsonResponse({'ok': True})


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_pedido_edit_lock_release(request, pedido_id):
	if request.method != 'POST':
		return JsonResponse({'ok': False}, status=405)

	release_pedido_edit_lock(pedido_id=pedido_id, user=request.user)
	return JsonResponse({'ok': True})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_picking_ticket(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente__usuario', 'seleccionador').prefetch_related('items__presentacion__producto'), id=pedido_id)
	return render(request, 'backoffice/picking_ticket.html', {
		'pedido': pedido,
		'picking_items': order_pedido_items_for_display(pedido),
		'pedido_estado_label': _pedido_state_label(pedido.estado),
	})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_picking_pdf(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'), id=pedido_id)

	buffer = BytesIO()
	document = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
	styles = getSampleStyleSheet()
	summary_label_style = ParagraphStyle('PickingSummaryLabel', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=9, textColor=BRAND_MUTED_TEXT, leading=11)
	summary_value_style = ParagraphStyle('PickingSummaryValue', parent=styles['BodyText'], fontSize=10, textColor=BRAND_TEXT, leading=12)
	document_date = format_local_datetime(timezone.now())

	content = [
		build_pdf_brand_banner(
			styles=styles,
			title=_("Picking Ticket"),
			subtitle=f'PO #{pedido.numero_display}',
			document_date=document_date,
			total_width=540,
		),
		Spacer(1, 12),
		Table([
			[Paragraph(_("Date"), summary_label_style), Paragraph(document_date or '-', summary_value_style)],
			[Paragraph(_("Customer"), summary_label_style), Paragraph(pedido.cliente.nombre_empresa, summary_value_style)],
			[Paragraph(_("Contact"), summary_label_style), Paragraph(pedido.cliente.usuario.get_full_name() or pedido.cliente.usuario.username, summary_value_style)],
			[Paragraph(_("Received date"), summary_label_style), Paragraph(format_local_datetime(pedido.creada_en) or '-', summary_value_style)],
			[Paragraph(_("Status"), summary_label_style), Paragraph(_pedido_state_label(pedido.estado), summary_value_style)],
			[Paragraph(_("Customer note"), summary_label_style), Paragraph(pedido.nota_cliente or '-', summary_value_style)],
		], colWidths=[120, 384]),
		Spacer(1, 16),
	]
	content[2].setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('INNERGRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('BACKGROUND', (0, 0), (-1, -1), BRAND_SURFACE),
		('LEFTPADDING', (0, 0), (-1, -1), 10),
		('RIGHTPADDING', (0, 0), (-1, -1), 10),
		('TOPPADDING', (0, 0), (-1, -1), 8),
		('BOTTOMPADDING', (0, 0), (-1, -1), 8),
	]))

	rows = [[
		Paragraph(escape(_('Product')), ParagraphStyle('PickingHeaderCell', parent=summary_label_style, textColor=colors.white, fontSize=9)),
		Paragraph(escape(_('Presentation')), ParagraphStyle('PickingHeaderCellPresentation', parent=summary_label_style, textColor=colors.white, fontSize=9)),
		Paragraph(escape(_('Quantity')), ParagraphStyle('PickingHeaderCellQuantity', parent=summary_label_style, textColor=colors.white, fontSize=9)),
		Paragraph(escape(_('Warehouse check')), ParagraphStyle('PickingHeaderCellCheck', parent=summary_label_style, textColor=colors.white, fontSize=9)),
	]]
	item_cell_style = ParagraphStyle(
		'PickingItemCell',
		parent=styles['BodyText'],
		fontSize=9,
		leading=11,
		textColor=BRAND_TEXT,
		wordWrap='CJK',
	)
	for item in order_pedido_items_for_display(pedido):
		rows.append([
			Paragraph(escape(item.presentacion.producto.nombre), item_cell_style),
			Paragraph(escape(item.presentacion.nombre_empaque_cliente), item_cell_style),
			Paragraph(escape(str(item.cantidad)), item_cell_style),
			Paragraph('______', item_cell_style),
		])

	table = Table(rows, colWidths=[210, 130, 60, 110])
	table.setStyle(TableStyle([
		('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
		('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BRAND_SURFACE]),
		('VALIGN', (0, 0), (-1, -1), 'TOP'),
		('LEFTPADDING', (0, 0), (-1, -1), 6),
		('RIGHTPADDING', (0, 0), (-1, -1), 6),
		('BOTTOMPADDING', (0, 0), (-1, -1), 8),
		('TOPPADDING', (0, 0), (-1, -1), 8),
	]))
	content.append(table)
	content.append(Spacer(1, 12))
	picking_items = order_pedido_items_for_display(pedido)
	shipment_summary = with_total_pallets(
		build_shipment_summary_from_pedido_items(picking_items, quantity_attr='cantidad'),
		pedido.cantidad_pallets,
	)
	summary_box_style = ParagraphStyle(
		'PickingShipmentSummaryLabel',
		parent=styles['BodyText'],
		fontName='Helvetica-Bold',
		fontSize=8,
	)
	summary_value_style = ParagraphStyle(
		'PickingShipmentSummaryValue',
		parent=styles['BodyText'],
		fontSize=8,
	)
	content.append(
		_build_invoice_pdf_shipment_summary_table(
			shipment_summary,
			box_style=summary_box_style,
			value_style=summary_value_style,
			total_width=504,
		)
	)

	document.build(content)
	pdf = buffer.getvalue()
	buffer.close()

	response = HttpResponse(pdf, content_type='application/pdf')
	response['Content-Disposition'] = f'attachment; filename="picking-ticket-{pedido.id}.pdf"'
	return response


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_inventory_needs_pdf(request, pedido_id):
	pedido = get_object_or_404(
		Pedido.objects.select_related('cliente__usuario').prefetch_related(
			'items__presentacion__producto',
			'items__presentacion__stock_operativo',
		),
		id=pedido_id,
	)
	analysis = build_pedido_inventory_needs_analysis(pedido=pedido)
	document_date = format_local_datetime(timezone.now()) or '-'

	buffer = BytesIO()
	document = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
	styles = getSampleStyleSheet()
	summary_label_style = ParagraphStyle(
		'InventoryNeedsSummaryLabel',
		parent=styles['BodyText'],
		fontName='Helvetica-Bold',
		fontSize=9,
		textColor=BRAND_MUTED_TEXT,
		leading=11,
	)
	summary_value_style = ParagraphStyle(
		'InventoryNeedsSummaryValue',
		parent=styles['BodyText'],
		fontSize=10,
		textColor=BRAND_TEXT,
		leading=12,
	)
	header_cell_style = ParagraphStyle(
		'InventoryNeedsHeaderCell',
		parent=summary_label_style,
		textColor=colors.white,
		fontSize=8,
	)
	item_cell_style = ParagraphStyle(
		'InventoryNeedsItemCell',
		parent=styles['BodyText'],
		fontSize=8,
		leading=10,
		textColor=BRAND_TEXT,
		wordWrap='CJK',
	)

	content = [
		build_pdf_brand_banner(
			styles=styles,
			title=_('Inventory Needs Report'),
			subtitle=f'PO #{pedido.numero_display}',
			document_date=document_date,
			total_width=540,
		),
		Spacer(1, 12),
		Table([
			[Paragraph(_('Order'), summary_label_style), Paragraph(f'#{pedido.numero_display}', summary_value_style)],
			[Paragraph(_('Customer'), summary_label_style), Paragraph(pedido.cliente.nombre_empresa, summary_value_style)],
			[Paragraph(_('Date'), summary_label_style), Paragraph(document_date, summary_value_style)],
			[
				Paragraph(_('Products to purchase'), summary_label_style),
				Paragraph(str(analysis['needs_purchase_count']), summary_value_style),
			],
			[
				Paragraph(_('Total CS to buy'), summary_label_style),
				Paragraph(str(analysis['total_to_buy']), summary_value_style),
			],
		], colWidths=[140, 364]),
		Spacer(1, 16),
	]
	content[2].setStyle(TableStyle([
		('BOX', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('INNERGRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('BACKGROUND', (0, 0), (-1, -1), BRAND_SURFACE),
		('LEFTPADDING', (0, 0), (-1, -1), 10),
		('RIGHTPADDING', (0, 0), (-1, -1), 10),
		('TOPPADDING', (0, 0), (-1, -1), 8),
		('BOTTOMPADDING', (0, 0), (-1, -1), 8),
	]))

	rows = [[
		Paragraph(escape(_('Product')), header_cell_style),
		Paragraph(escape(_('SKU')), header_cell_style),
		Paragraph(escape(_('Requested')), header_cell_style),
		Paragraph(escape(_('Stock')), header_cell_style),
		Paragraph(escape(_('To buy')), header_cell_style),
		Paragraph(escape(_('Status')), header_cell_style),
	]]
	for row in analysis['rows']:
		rows.append([
			Paragraph(
				escape(f"{row['product_name']} — {row['presentation_name']}"),
				item_cell_style,
			),
			Paragraph(escape(row['sku'] or '-'), item_cell_style),
			Paragraph(escape(f"{row['requested_quantity']} CS"), item_cell_style),
			Paragraph(escape(f"{row['available_stock']} CS"), item_cell_style),
			Paragraph(escape(f"{row['to_buy_quantity']} CS"), item_cell_style),
			Paragraph(escape(str(row['status_label'])), item_cell_style),
		])

	table = Table(rows, colWidths=[170, 70, 55, 55, 55, 99])
	table_style = [
		('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
		('GRID', (0, 0), (-1, -1), 0.5, BRAND_BORDER),
		('VALIGN', (0, 0), (-1, -1), 'TOP'),
		('LEFTPADDING', (0, 0), (-1, -1), 5),
		('RIGHTPADDING', (0, 0), (-1, -1), 5),
		('BOTTOMPADDING', (0, 0), (-1, -1), 6),
		('TOPPADDING', (0, 0), (-1, -1), 6),
	]
	for index, row in enumerate(analysis['rows'], start=1):
		if row['status'] == 'out_of_stock':
			table_style.append(('BACKGROUND', (0, index), (-1, index), colors.Color(1, 0.92, 0.92)))
		elif row['status'] == 'insufficient':
			table_style.append(('BACKGROUND', (0, index), (-1, index), colors.Color(1, 0.96, 0.88)))
		else:
			table_style.append(('BACKGROUND', (0, index), (-1, index), colors.white if index % 2 else BRAND_SURFACE))
	table.setStyle(TableStyle(table_style))
	content.append(table)
	content.append(Spacer(1, 10))
	content.append(
		Paragraph(
			escape(_('Use this report to purchase the missing packages and update inventory before dispatch.')),
			ParagraphStyle('InventoryNeedsFooter', parent=styles['BodyText'], fontSize=8, textColor=BRAND_MUTED_TEXT),
		)
	)

	document.build(content)
	pdf = buffer.getvalue()
	buffer.close()

	response = HttpResponse(pdf, content_type='application/pdf')
	response['Content-Disposition'] = f'attachment; filename="inventory-needs-order-{pedido.id}.pdf"'
	return response


@login_required
@internal_permission_required('selector.picking.view')
def selector_picking_list(request):
	if not _is_selector_user(request.user):
		return redirect('login')

	base_queryset = _selector_pedidos_queryset(request.user)
	search_query = str(request.GET.get('q') or '').strip()
	filtered_queryset = _filter_selector_picking_queryset(base_queryset, search_query)
	view_mode = request.GET.get('view')
	if view_mode == 'completed':
		pedidos = (
			filtered_queryset.exclude(estado='PARA_VERIFICAR')
			.annotate(picker_done_rank=_selector_picking_done_rank_annotation())
			.order_by('picker_done_rank', '-actualizada_en', '-creada_en')
		)
	else:
		view_mode = 'active'
		pedidos = filtered_queryset.filter(estado='PARA_VERIFICAR')

	_annotate_selector_picking_rows(pedidos)
	return render(request, 'backoffice/selector_picking_list.html', {
		'pedidos': pedidos,
		'view_mode': view_mode,
		'search_query': search_query,
		'active_count': base_queryset.filter(estado='PARA_VERIFICAR').count(),
		'completed_count': base_queryset.exclude(estado='PARA_VERIFICAR').count(),
	})


@login_required
@internal_permission_required('selector.picking.view')
def selector_picking_detail(request, pedido_id):
	if not _is_selector_user(request.user):
		return redirect('login')

	pedido = get_object_or_404(_selector_pedidos_queryset(request.user), id=pedido_id)
	pedido = Pedido.objects.select_related('cliente', 'seleccionador').prefetch_related('items__presentacion__producto__presentaciones', 'items__presentacion__stock_operativo').get(id=pedido.id)
	posted_quantities = None
	posted_presentations = None
	form_note = pedido.nota_seleccionador
	form_note_resolved = pedido.nota_seleccionador_resuelta
	additional_item_rows = []
	reviewed_item_ids = set()
	picking_progress = pedido.picking_progress or {}
	if picking_progress:
		posted_quantities = {
			int(item_id): _parse_non_negative_quantity(quantity, 0)
			for item_id, quantity in (picking_progress.get('quantities') or {}).items()
		}
		posted_presentations = {
			int(item_id): int(presentation_id)
			for item_id, presentation_id in (picking_progress.get('presentations') or {}).items()
		}
		form_note = str(picking_progress.get('note') or '')
		form_note_resolved = bool(picking_progress.get('note_resolved'))
		reviewed_item_ids = {
			int(item_id) for item_id in (picking_progress.get('reviewed_item_ids') or [])
		}
		additional_item_rows = list(picking_progress.get('additional_items') or [])
		saved_pallets = picking_progress.get('pallets')
		if saved_pallets not in (None, ''):
			pedido.cantidad_pallets = _parse_decimal(saved_pallets, 0)
	saved_quantities = _saved_selector_picking_quantities(pedido)
	initial_quantities = posted_quantities or saved_quantities or {item.id: 0 for item in pedido.items.all()}
	stock_evaluation = evaluar_stock_fisico_verificacion_picking(
		pedido_items=list(pedido.items.all()),
		cantidades_reales=initial_quantities,
	)
	form_has_stock_shortage = any(item_result['has_shortage'] for item_result in stock_evaluation.values())

	if request.method == 'POST':
		posted_presentations = {
			item.id: int(request.POST.get(f'presentacion_{item.id}') or item.presentacion_id)
			for item in pedido.items.all()
		}
		cantidades_reales = {
			item.id: _parse_non_negative_quantity(request.POST.get(f'cantidad_real_{item.id}'), 0)
			for item in pedido.items.all()
		}
		# POST is the source of truth for added products. Discard draft-seeded
		# rows so a second "Save progress" does not append the same lines again.
		additional_item_rows = []
		additional_items = []
		posted_new_presentations = request.POST.getlist('presentacion_nueva[]')
		posted_new_quantities = request.POST.getlist('cantidad_nueva[]')
		if not posted_new_presentations and request.POST.get('presentacion_nueva'):
			posted_new_presentations = [request.POST.get('presentacion_nueva')]
			posted_new_quantities = [request.POST.get('cantidad_nueva') or '1']
		for index, presentacion_id in enumerate(posted_new_presentations):
			presentacion_id = (presentacion_id or '').strip()
			quantity_value = posted_new_quantities[index] if index < len(posted_new_quantities) else '1'
			quantity = max(_parse_non_negative_quantity(quantity_value, 1), 1)
			if not presentacion_id:
				if quantity_value:
					additional_item_rows.append({'presentacion_id': '', 'cantidad': quantity})
				continue
			additional_items.append({'presentacion_id': int(presentacion_id), 'cantidad': quantity})
			additional_item_rows.append({'presentacion_id': presentacion_id, 'cantidad': quantity})

		stock_evaluation = evaluar_stock_fisico_verificacion_picking(
			pedido_items=list(pedido.items.all()),
			cantidades_reales=cantidades_reales,
		)
		form_has_stock_shortage = any(item_result['has_shortage'] for item_result in stock_evaluation.values())
		posted_quantities = {item_id: value for item_id, value in cantidades_reales.items() if isinstance(item_id, int)}
		nota = request.POST.get('nota_seleccionador')
		nota_resuelta = request.POST.get('nota_seleccionador_resuelta') == 'on' and not form_has_stock_shortage
		form_note = nota
		form_note_resolved = nota_resuelta
		reviewed_item_ids = {
			item.id for item in pedido.items.all()
			if request.POST.get(f'linea_revisada_{item.id}') == 'on'
		}

		if request.POST.get('submit_action') == 'save_progress':
			reviewed_additional_count = len(request.POST.getlist('linea_revisada_adicional[]'))
			for index, row in enumerate(additional_item_rows):
				row['reviewed'] = index < reviewed_additional_count
			pedido.picking_progress = {
				'quantities': {str(item_id): quantity for item_id, quantity in cantidades_reales.items()},
				'presentations': {str(item_id): presentation_id for item_id, presentation_id in posted_presentations.items()},
				'reviewed_item_ids': sorted(reviewed_item_ids),
				'additional_items': additional_item_rows,
				'note': nota or '',
				'note_resolved': nota_resuelta,
				'pallets': str(request.POST.get('cantidad_pallets') or ''),
			}
			pedido.picking_progress_saved_at = timezone.now()
			pedido.save(update_fields=['picking_progress', 'picking_progress_saved_at', 'actualizada_en'])
			messages.success(request, _('Picking progress saved. You can continue this verification later.'))
			return redirect('selector_picking_detail', pedido_id=pedido.id)

		try:
			_validate_selector_line_reviews(
				request,
				pedido=pedido,
				posted_new_presentations=posted_new_presentations,
				cantidades_reales=cantidades_reales,
				presentacion_updates=posted_presentations,
			)
			guardar_verificacion_picking(
				pedido=pedido,
				seleccionador=request.user,
				cantidades_reales=cantidades_reales,
				nota=nota,
				nota_resuelta=nota_resuelta,
				presentacion_updates=posted_presentations,
				additional_items=additional_items,
				cantidad_pallets=request.POST.get('cantidad_pallets'),
			)
		except (PermissionDenied, ValidationError) as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		else:
			if form_has_stock_shortage:
				messages.warning(request, _('Physical stock is insufficient. The verification was saved and the order remains blocked for BackOffice review.'))
			else:
				messages.success(request, _('Picking ticket verified successfully.'))
			Pedido.objects.filter(id=pedido.id).update(picking_progress={}, picking_progress_saved_at=None)
			return redirect(f"{reverse('selector_picking_list')}?view=completed")

	context = {
		'pedido': pedido,
		'pedido_estado_label': _pedido_state_label(pedido.estado),
		'pedido_lock_preview': form_has_stock_shortage or pedido.picking_bloqueado,
		'item_rows': _build_selector_item_rows(pedido, posted_quantities, posted_presentations),
		'picker_requires_full_line_review': not bool(pedido.picking_verificado_en),
		'form_note': form_note,
		'form_note_resolved': form_note_resolved,
		'form_has_stock_shortage': form_has_stock_shortage,
		'additional_item_rows': additional_item_rows,
		'reviewed_item_ids': reviewed_item_ids,
		'available_presentations': Presentacion.objects.select_related('producto', 'stock_operativo').filter(producto__activo=True).order_by('producto__nombre', 'nombre'),
	}
	return render(request, 'backoffice/selector_picking_detail.html', context)
