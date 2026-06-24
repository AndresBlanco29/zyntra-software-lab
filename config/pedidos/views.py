from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils.translation import gettext as _

from config.core.pdf_branding import (
	BRAND_BORDER,
	BRAND_MUTED_TEXT,
	BRAND_PRIMARY,
	BRAND_SURFACE,
	BRAND_TEXT,
	build_pdf_brand_banner,
)
from config.core.workflow_badges import build_order_workflow_badge
from config.usuarios.permissions import internal_permission_required
from config.usuarios.models import Usuario
from config.inventario.services import ajustar_cantidad_item_pedido_despues_picking, ajustar_reserva_item_pedido, reemplazar_presentacion_item_pedido, reemplazar_presentacion_item_pedido_despues_picking

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.facturacion.models import NotaAjuste
from config.facturacion.services import DEFAULT_SUGGESTED_PROFIT_PERCENTAGE, resolve_presentacion_suggested_unit_price, summarize_pending_customer_notes
from config.integrations.quickbooks.services import get_connection_status
from config.integrations.quickbooks.views import get_dashboard_sync_context
from config.notificaciones.models import Notificacion
from config.productos.models import Presentacion
from config.inventario.models import StockPresentacion

from .models import Pedido, PedidoItem
from .dispatch_orders import build_dispatch_order_page, get_dispatch_order_counts
from .services import (
	actualizar_cantidad_linea_pedido_sin_aplicar_inventario,
	anular_pedido_desde_backoffice,
	asignar_picking_a_seleccionador,
	build_pedido_edit_lock_context,
	eliminar_linea_pedido_desde_backoffice,
	eliminar_pedido_desde_backoffice,
	ensure_pedido_edit_lock_owner,
	evaluar_stock_fisico_verificacion_picking,
	guardar_verificacion_picking,
	puede_anular_pedido_desde_backoffice,
	puede_eliminar_pedido_desde_backoffice,
	recalcular_pedido,
	refresh_pedido_edit_lock,
	reemplazar_presentacion_linea_pedido_sin_aplicar_inventario,
	release_pedido_edit_lock,
	validar_estado_backoffice_con_bloqueo,
)


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
		'VENDEDOR': _('Vendor'),
	}.get(origin, origin)


def _pedido_state_choices():
	return [(code, _pedido_state_label(code)) for code, _label in Pedido.ESTADO_CHOICES]


def _selector_pedidos_queryset(user):
	queryset = Pedido.objects.select_related('cliente__usuario', 'seleccionador').prefetch_related('items__presentacion__producto').order_by('-actualizada_en', '-creada_en')
	if user.is_superuser or getattr(user, 'role', '') == 'admin':
		return queryset.filter(seleccionador__isnull=False)
	return queryset.filter(seleccionador=user)


def _build_selector_item_rows(pedido, actual_quantity_overrides=None, presentation_overrides=None):
	rows = []
	actual_quantity_overrides = actual_quantity_overrides or {}
	presentation_overrides = presentation_overrides or {}
	for item in pedido.items.select_related('presentacion__producto').all():
		product_presentations = item.presentacion.producto.presentaciones.order_by('nombre')
		rows.append({
			'id': item.id,
			'product': item.presentacion.producto.nombre,
			'presentation': item.presentacion.nombre,
			'presentation_id': presentation_overrides.get(item.id, item.presentacion_id),
			'presentation_options': [
				{
					'id': presentation.id,
					'label': presentation.nombre_traducido,
				}
				for presentation in product_presentations
			],
			'requested_quantity': item.cantidad_solicitada,
			'actual_quantity': actual_quantity_overrides.get(item.id, item.cantidad),
			'stock_physical': int(getattr(getattr(item.presentacion, 'stock_operativo', None), 'stock_fisico', 0) or 0),
			'applied_quantity': int(item.cantidad_inventario_aplicada or 0),
		})
	return rows


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
	view_mode, page_obj = build_dispatch_order_page(
		view_mode=request.GET.get('view'),
		page_number=request.GET.get('page'),
		page_size=BACKOFFICE_PEDIDOS_PAGE_SIZE,
	)
	counts = get_dispatch_order_counts()
	return render(request, 'backoffice/pedidos_lista.html', {
		'dispatch_orders': page_obj,
		'page_obj': page_obj,
		'view_mode': view_mode,
		**counts,
	})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_pedido_detalle(request, pedido_id):
	pedido = get_object_or_404(
		Pedido.objects.select_related('cliente__usuario', 'vendedor', 'seleccionador', 'invoice', 'invoice__driver').prefetch_related('items__presentacion__producto', 'items__presentacion__stock_operativo', 'items__selector_original_presentacion'),
		id=pedido_id,
	)
	pedido_items = list(pedido.items.select_related('presentacion__producto', 'presentacion__stock_operativo'))
	pedido.workflow_badge = build_order_workflow_badge(pedido)
	picker_stock_evaluation = evaluar_stock_fisico_verificacion_picking(
		pedido_items=pedido_items,
		cantidades_reales={item.id: item.cantidad for item in pedido_items},
	)
	picker_stock_shortage_rows = [
		{
			'product_name': item.presentacion.producto.nombre,
			'presentation_name': item.presentacion.nombre,
			'quantity_to_pick': item.cantidad,
			'available_physical_stock': picker_stock_evaluation[item.id]['stock_fisico'],
			'shortage_amount': picker_stock_evaluation[item.id]['shortage_amount'],
		}
		for item in pedido_items
		if picker_stock_evaluation[item.id]['has_shortage']
	]
	for item in pedido_items:
		item.presentation_options = list(item.presentacion.producto.presentaciones.order_by('nombre'))

	if request.method == 'POST':
		if not request.user.has_internal_permission('backoffice.orders.manage'):
			return redirect('backoffice_pedidos')
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

				lineas_bloqueadas = bool(pedido.seleccionador_id and pedido.estado == 'PARA_VERIFICAR')
				if not lineas_bloqueadas:
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
						item.subtotal = item.precio * item.cantidad
						item.save(update_fields=['precio', 'subtotal'])

					nueva_presentacion_id = request.POST.get('presentacion_nueva')
					if nueva_presentacion_id:
						presentacion = get_object_or_404(Presentacion.objects.select_related('producto'), id=nueva_presentacion_id)
						cantidad_nueva = _parse_quantity(request.POST.get('cantidad_nueva'), 1)
						precio_nuevo = _parse_decimal(request.POST.get('precio_nuevo'), 0)
						PedidoItem.objects.create(
							pedido=pedido,
							presentacion=presentacion,
							cantidad_solicitada=cantidad_nueva,
							cantidad=cantidad_nueva,
							precio=precio_nuevo,
							subtotal=precio_nuevo * cantidad_nueva,
						)

					recalcular_pedido(pedido)
		except ValidationError as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
			return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

		release_pedido_edit_lock(pedido=pedido, user=request.user)
		messages.success(request, _('Sales order updated successfully.'))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	edit_lock_context = build_pedido_edit_lock_context(pedido=pedido, user=request.user)
	pedido_form_disabled = (
		edit_lock_context['pedido_edit_blocked']
		or bool(pedido.seleccionador_id and pedido.estado == 'PARA_VERIFICAR')
		or hasattr(pedido, 'invoice')
	)
	can_manage_pedido = (
		request.user.has_internal_permission('backoffice.orders.manage')
		and not edit_lock_context['pedido_edit_blocked']
		and not pedido_form_disabled
	)

	context = {
		'pending_customer_notes_summary': summarize_pending_customer_notes(cliente=pedido.cliente),
		'pedido': pedido,
		'pedido_items': pedido_items,
		'pedido_has_picker_changes': any(_item_has_picker_change_banner(item, pedido) for item in pedido_items),
		'invoice': getattr(pedido, 'invoice', None),
		'picker_stock_shortage_blocked': bool(pedido.picking_bloqueado and picker_stock_shortage_rows),
		'picker_stock_shortage_rows': picker_stock_shortage_rows,
		'pedido_estado_label': _pedido_state_label(pedido.estado),
		'pedido_origen_label': _pedido_origin_label(pedido.origen),
		'state_choices': _pedido_state_choices(),
		'drivers': Usuario.objects.filter(role='driver', is_active=True).order_by('first_name', 'last_name', 'username'),
		'selectores': Usuario.objects.filter(role='seleccionador', is_active=True).order_by('first_name', 'last_name', 'username'),
		'lineas_bloqueadas_para_picking': bool(pedido.seleccionador_id and pedido.estado == 'PARA_VERIFICAR') or hasattr(pedido, 'invoice'),
		'pedido_form_disabled': pedido_form_disabled,
		'can_manage_pedido': can_manage_pedido,
		'can_void_pedido': puede_anular_pedido_desde_backoffice(pedido) and can_manage_pedido,
		'can_delete_pedido': puede_eliminar_pedido_desde_backoffice(pedido) and can_manage_pedido,
		'invoice_suggested_price_rows': [
			{
				'item_id': item.id,
				'product_name': item.presentacion.producto.nombre,
				'presentation_name': item.presentacion.nombre,
				'quantity': item.cantidad,
				'base_unit_value': format(_pedido_item_customer_unit_price(item), '.2f'),
				'list_unit_value': format(item.precio, '.2f'),
				'default_discount': max(int(getattr(item.presentacion.producto, 'descuento', 0) or 0), 0),
				'default_value': format(resolve_presentacion_suggested_unit_price(presentacion=item.presentacion, base_case_price=item.precio), '.2f'),
				'default_percentage': format(DEFAULT_SUGGESTED_PROFIT_PERCENTAGE, '.2f'),
			}
			for item in pedido.items.select_related('presentacion__producto')
			if item.cantidad > 0
		],
		**edit_lock_context,
	}
	return render(request, 'backoffice/pedido_detalle.html', context)


def _default_presentacion_price_for_pedido(*, presentacion, pedido):
	cliente = getattr(pedido, 'cliente', None)
	tier = cliente.get_nivel_precio_normalizado() if cliente and hasattr(cliente, 'get_nivel_precio_normalizado') else None
	price = presentacion.get_price_for_tier(tier)
	if price is None:
		price = presentacion.precio_1
	return _quantize_money(price or 0)


def _default_presentacion_price_key_for_pedido(*, pedido):
	cliente = getattr(pedido, 'cliente', None)
	tier = cliente.get_nivel_precio_normalizado() if cliente and hasattr(cliente, 'get_nivel_precio_normalizado') else None
	if tier is None:
		return 'precio_1'
	return f'precio_{tier}'


def _build_presentacion_price_options(*, presentacion, pedido=None):
	prices = []
	for index in range(1, 6):
		key = f'precio_{index}'
		value = _quantize_money(getattr(presentacion, key, 0) or 0)
		prices.append({
			'key': key,
			'value': format(value, '.2f'),
			'label': f'{_("Price")} {index} - ${format(value, ".2f")}',
		})

	default_key = _default_presentacion_price_key_for_pedido(pedido=pedido) if pedido else 'precio_1'
	default_price = _default_presentacion_price_for_pedido(presentacion=presentacion, pedido=pedido) if pedido else _quantize_money(presentacion.precio_1 or 0)
	return prices, default_key, format(default_price, '.2f')


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
	if pedido_id.isdigit():
		pedido = Pedido.objects.select_related('cliente').filter(id=int(pedido_id)).first()

	results = []
	for presentacion in presentaciones:
		price_options, default_price_key, default_price = _build_presentacion_price_options(
			presentacion=presentacion,
			pedido=pedido,
		)
		results.append({
			'id': presentacion.id,
			'label': f'{presentacion.producto.nombre} - {presentacion.nombre}',
			'price': default_price,
			'default_price_key': default_price_key,
			'prices': price_options,
		})

	return JsonResponse({'results': results})


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_pedido_void(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	try:
		ensure_pedido_edit_lock_owner(pedido=pedido, user=request.user)
		anular_pedido_desde_backoffice(pedido=pedido)
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
		asignar_picking_a_seleccionador(pedido=pedido, seleccionador=seleccionador)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		release_pedido_edit_lock(pedido=pedido, user=request.user)
		messages.success(request, _('Picking ticket sent to selector successfully.'))

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

	content = [
		build_pdf_brand_banner(styles=styles, title=_("Picking Ticket"), subtitle=f'PO #{pedido.id}', total_width=540),
		Spacer(1, 12),
		Table([
			[Paragraph(_("Customer"), summary_label_style), Paragraph(pedido.cliente.nombre_empresa, summary_value_style)],
			[Paragraph(_("Contact"), summary_label_style), Paragraph(pedido.cliente.usuario.get_full_name() or pedido.cliente.usuario.username, summary_value_style)],
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
	for item in pedido.items.all():
		rows.append([
			Paragraph(escape(item.presentacion.producto.nombre), item_cell_style),
			Paragraph(escape(item.presentacion.nombre), item_cell_style),
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

	document.build(content)
	pdf = buffer.getvalue()
	buffer.close()

	response = HttpResponse(pdf, content_type='application/pdf')
	response['Content-Disposition'] = f'attachment; filename="picking-ticket-{pedido.id}.pdf"'
	return response


@login_required
@internal_permission_required('selector.picking.view')
def selector_picking_list(request):
	if not _is_selector_user(request.user):
		return redirect('login')

	base_queryset = _selector_pedidos_queryset(request.user)
	view_mode = request.GET.get('view')
	if view_mode == 'completed':
		pedidos = base_queryset.exclude(estado='PARA_VERIFICAR')
	else:
		view_mode = 'active'
		pedidos = base_queryset.filter(estado='PARA_VERIFICAR')

	for pedido in pedidos:
		pedido.estado_label = _pedido_state_label(pedido.estado)
		pedido.workflow_badge = build_order_workflow_badge(pedido)
	return render(request, 'backoffice/selector_picking_list.html', {
		'pedidos': pedidos,
		'view_mode': view_mode,
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
	stock_evaluation = evaluar_stock_fisico_verificacion_picking(
		pedido_items=list(pedido.items.all()),
		cantidades_reales={item.id: item.cantidad for item in pedido.items.all()},
	)
	form_has_stock_shortage = any(item_result['has_shortage'] for item_result in stock_evaluation.values())

	if request.method == 'POST':
		posted_presentations = {
			item.id: int(request.POST.get(f'presentacion_{item.id}') or item.presentacion_id)
			for item in pedido.items.all()
		}
		cantidades_reales = {
			item.id: _parse_non_negative_quantity(request.POST.get(f'cantidad_real_{item.id}'), item.cantidad)
			for item in pedido.items.all()
		}
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

		try:
			guardar_verificacion_picking(
				pedido=pedido,
				seleccionador=request.user,
				cantidades_reales=cantidades_reales,
				nota=nota,
				nota_resuelta=nota_resuelta,
				presentacion_updates=posted_presentations,
				additional_items=additional_items,
			)
		except (PermissionDenied, ValidationError) as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		else:
			if form_has_stock_shortage:
				messages.warning(request, _('Physical stock is insufficient. The verification was saved and the order remains blocked for BackOffice review.'))
			else:
				messages.success(request, _('Picking ticket verified successfully.'))
			return redirect('selector_picking_list')

	context = {
		'pedido': pedido,
		'pedido_estado_label': _pedido_state_label(pedido.estado),
		'pedido_lock_preview': form_has_stock_shortage or pedido.picking_bloqueado,
		'item_rows': _build_selector_item_rows(pedido, posted_quantities, posted_presentations),
		'form_note': form_note,
		'form_note_resolved': form_note_resolved,
		'form_has_stock_shortage': form_has_stock_shortage,
		'additional_item_rows': additional_item_rows,
		'available_presentations': Presentacion.objects.select_related('producto', 'stock_operativo').filter(producto__activo=True).order_by('producto__nombre', 'nombre'),
	}
	return render(request, 'backoffice/selector_picking_detail.html', context)
