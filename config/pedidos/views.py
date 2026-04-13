from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils.translation import gettext as _

from config.usuarios.permissions import internal_permission_required
from config.usuarios.models import Usuario
from config.inventario.services import ajustar_reserva_item_pedido, cancelar_pedido_con_inventario, eliminar_item_pedido_con_inventario, reservar_stock_para_pedido_items

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.cotizaciones.models import Cotizacion
from config.notificaciones.models import Notificacion
from config.productos.models import Presentacion

from .models import Pedido, PedidoItem
from .services import (
	asignar_picking_a_seleccionador,
	guardar_verificacion_picking,
	recalcular_pedido,
	validar_estado_backoffice_con_bloqueo,
)


def _is_backoffice_user(user):
	return bool(user and user.is_authenticated and (user.is_superuser or user.role in {'admin', 'backoffice'}))


def _is_selector_user(user):
	return bool(user and user.is_authenticated and (user.is_superuser or user.role in {'admin', 'seleccionador'}))


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


def _build_selector_item_rows(pedido):
	rows = []
	for item in pedido.items.select_related('presentacion__producto').all():
		rows.append({
			'id': item.id,
			'product': item.presentacion.producto.nombre,
			'presentation': item.presentacion.nombre,
			'requested_quantity': item.cantidad_solicitada,
			'actual_quantity': item.cantidad,
		})
	return rows


@login_required
@internal_permission_required('backoffice.dashboard.view')
def backoffice_dashboard(request):
	context = {
		'cotizaciones_pendientes': Cotizacion.objects.filter(estado='ENVIADA').count(),
		'ordenes_recibidas': Pedido.objects.count(),
		'ordenes_en_gestion': Pedido.objects.filter(estado='EN_GESTION').count(),
		'ordenes_listas_picking': Pedido.objects.filter(estado='LISTO_PARA_PICKING').count(),
		'notificaciones': Notificacion.objects.all()[:8],
	}
	return render(request, 'backoffice/dashboard.html', context)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_pedidos(request):
	pedidos = Pedido.objects.select_related('cliente__usuario', 'vendedor', 'seleccionador').prefetch_related('items').order_by('-creada_en')
	for pedido in pedidos:
		pedido.estado_label = _pedido_state_label(pedido.estado)
		pedido.origen_label = _pedido_origin_label(pedido.origen)
	return render(request, 'backoffice/pedidos_lista.html', {'pedidos': pedidos})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_pedido_detalle(request, pedido_id):
	pedido = get_object_or_404(
		Pedido.objects.select_related('cliente__usuario', 'vendedor', 'seleccionador', 'invoice', 'invoice__driver').prefetch_related('items__presentacion__producto'),
		id=pedido_id,
	)

	if request.method == 'POST':
		if not request.user.has_internal_permission('backoffice.orders.manage'):
			return redirect('backoffice_pedidos')
		try:
			with transaction.atomic():
				if hasattr(pedido, 'invoice'):
					raise ValidationError(_('Orders with a generated invoice are locked on this screen.'))
				estado_anterior = pedido.estado
				nuevo_estado = request.POST.get('estado') or pedido.estado
				validar_estado_backoffice_con_bloqueo(pedido, nuevo_estado)

				pedido.estado = nuevo_estado
				pedido.nota_backoffice = (request.POST.get('nota_backoffice') or '').strip()
				pedido.save(update_fields=['estado', 'nota_backoffice', 'actualizada_en'])

				lineas_bloqueadas = bool(pedido.seleccionador_id and pedido.estado in {'PARA_VERIFICAR', 'VERIFICADO_AJUSTADO'})
				if nuevo_estado == 'CANCELADO' and estado_anterior != 'CANCELADO':
					cancelar_pedido_con_inventario(pedido=pedido, creado_por=request.user)
				elif not lineas_bloqueadas:
					for item in list(pedido.items.select_related('presentacion__producto')):
						if request.POST.get(f'eliminar_{item.id}'):
							eliminar_item_pedido_con_inventario(item=item, creado_por=request.user)
							continue

						nueva_cantidad = _parse_quantity(request.POST.get(f'cantidad_{item.id}'), item.cantidad)
						item = ajustar_reserva_item_pedido(item=item, nueva_cantidad=nueva_cantidad, creado_por=request.user)
						item.precio = _parse_decimal(request.POST.get(f'precio_{item.id}'), item.precio)
						item.subtotal = item.precio * item.cantidad
						item.save(update_fields=['precio', 'subtotal'])

					nueva_presentacion_id = request.POST.get('presentacion_nueva')
					if nueva_presentacion_id:
						presentacion = get_object_or_404(Presentacion.objects.select_related('producto'), id=nueva_presentacion_id)
						cantidad_nueva = _parse_quantity(request.POST.get('cantidad_nueva'), 1)
						precio_nuevo = _parse_decimal(request.POST.get('precio_nuevo'), 0)
						nuevo_item = PedidoItem.objects.create(
							pedido=pedido,
							presentacion=presentacion,
							cantidad_solicitada=cantidad_nueva,
							cantidad=cantidad_nueva,
							precio=precio_nuevo,
							subtotal=precio_nuevo * cantidad_nueva,
						)
						reservar_stock_para_pedido_items(pedido=pedido, pedido_items=[nuevo_item], creado_por=request.user)

					recalcular_pedido(pedido)
		except ValidationError as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
			return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

		messages.success(request, _('Purchase order updated successfully.'))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	context = {
		'pedido': pedido,
		'invoice': getattr(pedido, 'invoice', None),
		'pedido_estado_label': _pedido_state_label(pedido.estado),
		'pedido_origen_label': _pedido_origin_label(pedido.origen),
		'state_choices': _pedido_state_choices(),
		'drivers': Usuario.objects.filter(role='driver', is_active=True).order_by('first_name', 'last_name', 'username'),
		'selectores': Usuario.objects.filter(role='seleccionador', is_active=True).order_by('first_name', 'last_name', 'username'),
		'lineas_bloqueadas_para_picking': bool(pedido.seleccionador_id and pedido.estado in {'PARA_VERIFICAR', 'VERIFICADO_AJUSTADO'}) or hasattr(pedido, 'invoice'),
		'presentaciones': Presentacion.objects.select_related('producto').filter(producto__activo=True).order_by('producto__nombre', 'nombre'),
	}
	return render(request, 'backoffice/pedido_detalle.html', context)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_asignar_picking(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente__usuario', 'seleccionador'), id=pedido_id)
	if request.method != 'POST':
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	selector_id = request.POST.get('seleccionador_id')
	seleccionador = get_object_or_404(Usuario, id=selector_id, role='seleccionador', is_active=True)

	try:
		asignar_picking_a_seleccionador(pedido=pedido, seleccionador=seleccionador)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	else:
		messages.success(request, _('Picking ticket sent to selector successfully.'))

	return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)


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

	content = [
		Paragraph(f'{_("Picking Ticket")} - PO #{pedido.id}', styles['Title']),
		Spacer(1, 12),
		Paragraph(f'{_("Customer")}: {pedido.cliente.nombre_empresa}', styles['Normal']),
		Paragraph(f'{_("Contact")}: {pedido.cliente.usuario.get_full_name() or pedido.cliente.usuario.username}', styles['Normal']),
		Paragraph(f'{_("Status")}: {_pedido_state_label(pedido.estado)}', styles['Normal']),
		Paragraph(f'{_("Customer note")}: {pedido.nota_cliente or "-"}', styles['Normal']),
		Spacer(1, 16),
	]

	rows = [[_('Product'), _('Presentation'), _('Quantity'), _('Warehouse check')]]
	for item in pedido.items.all():
		rows.append([
			item.presentacion.producto.nombre,
			item.presentacion.nombre,
			str(item.cantidad),
			'______',
		])

	table = Table(rows, colWidths=[180, 140, 70, 120])
	table.setStyle(TableStyle([
		('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0b3d91')),
		('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
		('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
		('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
		('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
		('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
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

	pedidos = _selector_pedidos_queryset(request.user)
	for pedido in pedidos:
		pedido.estado_label = _pedido_state_label(pedido.estado)
	return render(request, 'backoffice/selector_picking_list.html', {'pedidos': pedidos})


@login_required
@internal_permission_required('selector.picking.view')
def selector_picking_detail(request, pedido_id):
	if not _is_selector_user(request.user):
		return redirect('login')

	pedido = get_object_or_404(_selector_pedidos_queryset(request.user), id=pedido_id)

	if request.method == 'POST':
		cantidades_reales = {
			item.id: _parse_non_negative_quantity(request.POST.get(f'cantidad_real_{item.id}'), item.cantidad)
			for item in pedido.items.all()
		}
		nota = request.POST.get('nota_seleccionador')
		nota_resuelta = request.POST.get('nota_seleccionador_resuelta') == 'on'

		try:
			guardar_verificacion_picking(
				pedido=pedido,
				seleccionador=request.user,
				cantidades_reales=cantidades_reales,
				nota=nota,
				nota_resuelta=nota_resuelta,
			)
		except (PermissionDenied, ValidationError) as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		else:
			messages.success(request, _('Picking ticket verified successfully.'))
			return redirect('selector_picking_detail', pedido_id=pedido.id)

	context = {
		'pedido': pedido,
		'pedido_estado_label': _pedido_state_label(pedido.estado),
		'item_rows': _build_selector_item_rows(pedido),
	}
	return render(request, 'backoffice/selector_picking_detail.html', context)
