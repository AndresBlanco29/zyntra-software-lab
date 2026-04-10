from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils.translation import gettext as _

from config.usuarios.permissions import internal_permission_required

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.cotizaciones.models import Cotizacion
from config.notificaciones.models import Notificacion
from config.productos.models import Presentacion

from .models import Pedido, PedidoItem
from .services import recalcular_pedido


def _is_backoffice_user(user):
	return bool(user and user.is_authenticated and (user.is_superuser or user.role in {'admin', 'backoffice'}))


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


def _pedido_state_label(state):
	return {
		'RECIBIDO': _('Received'),
		'EN_GESTION': _('In progress'),
		'LISTO_PARA_PICKING': _('Ready for picking'),
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
	pedidos = Pedido.objects.select_related('cliente__usuario', 'vendedor').prefetch_related('items').order_by('-creada_en')
	for pedido in pedidos:
		pedido.estado_label = _pedido_state_label(pedido.estado)
		pedido.origen_label = _pedido_origin_label(pedido.origen)
	return render(request, 'backoffice/pedidos_lista.html', {'pedidos': pedidos})


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_pedido_detalle(request, pedido_id):
	pedido = get_object_or_404(
		Pedido.objects.select_related('cliente__usuario', 'vendedor').prefetch_related('items__presentacion__producto'),
		id=pedido_id,
	)

	if request.method == 'POST':
		if not request.user.has_internal_permission('backoffice.orders.manage'):
			return redirect('backoffice_pedidos')
		with transaction.atomic():
			pedido.estado = request.POST.get('estado') or pedido.estado
			pedido.nota_backoffice = (request.POST.get('nota_backoffice') or '').strip()
			pedido.save(update_fields=['estado', 'nota_backoffice', 'actualizada_en'])

			for item in list(pedido.items.select_related('presentacion__producto')):
				if request.POST.get(f'eliminar_{item.id}'):
					item.delete()
					continue

				item.cantidad = _parse_quantity(request.POST.get(f'cantidad_{item.id}'), item.cantidad)
				item.precio = _parse_decimal(request.POST.get(f'precio_{item.id}'), item.precio)
				item.subtotal = item.precio * item.cantidad
				item.save(update_fields=['cantidad', 'precio', 'subtotal'])

			nueva_presentacion_id = request.POST.get('presentacion_nueva')
			if nueva_presentacion_id:
				presentacion = get_object_or_404(Presentacion.objects.select_related('producto'), id=nueva_presentacion_id)
				cantidad_nueva = _parse_quantity(request.POST.get('cantidad_nueva'), 1)
				precio_nuevo = _parse_decimal(request.POST.get('precio_nuevo'), 0)
				PedidoItem.objects.create(
					pedido=pedido,
					presentacion=presentacion,
					cantidad=cantidad_nueva,
					precio=precio_nuevo,
					subtotal=precio_nuevo * cantidad_nueva,
				)

			recalcular_pedido(pedido)

		messages.success(request, _('Purchase order updated successfully.'))
		return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)

	context = {
		'pedido': pedido,
		'pedido_estado_label': _pedido_state_label(pedido.estado),
		'pedido_origen_label': _pedido_origin_label(pedido.origen),
		'state_choices': _pedido_state_choices(),
		'presentaciones': Presentacion.objects.select_related('producto').filter(producto__activo=True).order_by('producto__nombre', 'nombre'),
	}
	return render(request, 'backoffice/pedido_detalle.html', context)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_picking_ticket(request, pedido_id):
	pedido = get_object_or_404(Pedido.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'), id=pedido_id)
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
