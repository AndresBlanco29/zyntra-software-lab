from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config.pedidos.models import Pedido
from config.usuarios.models import Usuario
from config.usuarios.permissions import internal_permission_required

from .models import Delivery, Invoice, NotaAjuste
from .services import (
	aprobar_nota_ajuste,
	anular_nota_ajuste,
	build_google_maps_route_url,
	complete_driver_delivery,
	crear_nota_ajuste_desde_invoice,
	ensure_delivery_for_invoice,
	generar_invoice_desde_picking,
	start_delivery_route,
	unlock_client_from_delivery,
)


def _invoice_pdf_response(invoice):
	buffer = BytesIO()
	document = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
	styles = getSampleStyleSheet()

	content = [
		Paragraph(f'Invoice {invoice.numero}', styles['Title']),
		Spacer(1, 12),
		Paragraph(f'{_("Customer")}: {invoice.cliente.nombre_empresa}', styles['Normal']),
		Paragraph(f'{_("Purchase order")}: #{invoice.pedido_id}', styles['Normal']),
		Paragraph(f'{_("Delivery method")}: {invoice.get_metodo_entrega_display()}', styles['Normal']),
		Paragraph(f'{_("Driver")}: {(invoice.driver.get_full_name() or invoice.driver.username) if invoice.driver else "-"}', styles['Normal']),
		Paragraph(f'{_("Generated on")}: {timezone.localtime(invoice.creada_en).strftime("%d/%m/%Y %H:%M")}', styles['Normal']),
		Spacer(1, 16),
	]

	rows = [[_('Product'), _('Presentation'), _('Quantity'), _('Unit price'), _('Subtotal')]]
	for item in invoice.items.all():
		rows.append([
			item.producto_nombre,
			item.presentacion_nombre,
			str(item.cantidad_facturada),
			f'${item.precio_unitario}',
			f'${item.subtotal}',
		])

	table = Table(rows, colWidths=[165, 115, 60, 90, 90])
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
	content.append(Spacer(1, 16))

	totals = [
		Paragraph(f'{_("Subtotal")}: ${invoice.subtotal}', styles['Normal']),
		Paragraph(f'{_("Credits")}: ${invoice.total_creditos}', styles['Normal']),
		Paragraph(f'{_("Debits")}: ${invoice.total_debitos}', styles['Normal']),
		Paragraph(f'{_("Customer balance")}: ${invoice.saldo_cliente}', styles['Heading3']),
	]
	content.extend(totals)

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


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_invoices_list(request):
	invoices = Invoice.objects.select_related('pedido__cliente', 'driver', 'creada_por').prefetch_related('items', 'notas_ajuste').order_by('-creada_en')
	return render(request, 'backoffice/invoices_list.html', {'invoices': invoices})


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
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega=metodo_entrega,
			driver=driver,
			usuario=request.user,
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
	deliveries = Delivery.objects.select_related('invoice__cliente__usuario', 'driver').prefetch_related('invoice__items').filter(driver=request.user).order_by('estado', 'created_at')
	return render(request, 'backoffice/driver_delivery_list.html', {'deliveries': deliveries})


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
def driver_delivery_start_route(request, delivery_id):
	delivery = get_object_or_404(Delivery.objects.select_related('invoice__cliente'), id=delivery_id, driver=request.user)
	if request.method != 'POST':
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	try:
		start_delivery_route(delivery=delivery, driver_user=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('driver_delivery_detail', delivery_id=delivery.id)
	return redirect(delivery.google_maps_url)


@login_required
@internal_permission_required('driver.delivery.view')
def driver_delivery_route(request):
	deliveries = Delivery.objects.filter(driver=request.user, estado__in={'ASIGNADA', 'EN_RUTA'}).order_by('created_at')
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
