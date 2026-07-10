from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from config.usuarios.permissions import internal_permission_required

from .daily_closing import (
	actualizar_revision_item_cierre,
	agregar_invoices_al_cierre,
	build_invoice_closing_alerts,
	cerrar_cierre_diario,
	crear_cierre_diario,
	invoices_elegibles_para_cierre,
	liberar_items_cierre,
	recalcular_totales_cierre,
)
from .models import CierreDiario, CierreDiarioItem, NotaAjuste


def _parse_bool(value):
	return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_daily_closing_list(request):
	cierres = CierreDiario.objects.select_related('creado_por', 'cerrado_por').order_by('-fecha', '-id')
	page_obj = Paginator(cierres, 25).get_page(request.GET.get('page'))
	return render(request, 'backoffice/daily_closing_list.html', {
		'page_obj': page_obj,
		'cierres': page_obj,
		'today': timezone.localdate(),
		'can_manage': request.user.has_internal_permission('backoffice.orders.manage'),
	})


@login_required
@internal_permission_required('backoffice.orders.manage')
@require_POST
def backoffice_daily_closing_create(request):
	fecha = parse_date((request.POST.get('fecha') or '').strip()) or timezone.localdate()
	notas = (request.POST.get('notas') or '').strip()
	cierre = crear_cierre_diario(fecha=fecha, usuario=request.user, notas=notas)
	messages.success(request, _('Daily closing for %(fecha)s was created.') % {'fecha': fecha})
	return redirect('backoffice_daily_closing_detail', cierre_id=cierre.id)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_daily_closing_detail(request, cierre_id):
	cierre = get_object_or_404(
		CierreDiario.objects.select_related('creado_por', 'cerrado_por'),
		id=cierre_id,
	)
	items = list(
		cierre.items.select_related(
			'invoice__cliente',
			'invoice__delivery',
			'invoice__driver',
			'revisado_por',
		).order_by('invoice_id')
	)
	search = (request.GET.get('q') or '').strip()
	elegibles = []
	if cierre.is_editable and request.user.has_internal_permission('backoffice.orders.manage'):
		elegibles = list(invoices_elegibles_para_cierre(search=search)[:80])

	return render(request, 'backoffice/daily_closing_detail.html', {
		'cierre': cierre,
		'items': items,
		'elegibles': elegibles,
		'search_query': search,
		'can_manage': request.user.has_internal_permission('backoffice.orders.manage'),
		'quickbooks_center_url': reverse('quickbooks_center'),
	})


@login_required
@internal_permission_required('backoffice.orders.manage')
@require_POST
def backoffice_daily_closing_add_invoices(request, cierre_id):
	cierre = get_object_or_404(CierreDiario, id=cierre_id)
	try:
		created = agregar_invoices_al_cierre(
			cierre=cierre,
			invoice_ids=request.POST.getlist('invoice_ids'),
			usuario=request.user,
		)
		messages.success(
			request,
			_('%(count)s invoice(s) added to the daily closing.') % {'count': len(created)},
		)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	return redirect('backoffice_daily_closing_detail', cierre_id=cierre.id)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_daily_closing_item_review(request, cierre_id, item_id):
	cierre = get_object_or_404(CierreDiario, id=cierre_id)
	item = get_object_or_404(
		CierreDiarioItem.objects.select_related(
			'invoice__cliente',
			'invoice__delivery',
			'invoice__driver',
			'invoice__pedido',
			'revisado_por',
		),
		id=item_id,
		cierre=cierre,
	)
	invoice = item.invoice
	# Refresh alerts for display
	item.alertas = build_invoice_closing_alerts(invoice)
	notas_credito = list(
		NotaAjuste.objects.filter(invoice=invoice, tipo_documento='CREDITO').order_by('-id')[:10]
	)
	can_manage = (
		request.user.has_internal_permission('backoffice.orders.manage')
		and cierre.is_editable
		and item.estado != 'LIBERADA'
	)

	if request.method == 'POST':
		if not can_manage:
			messages.error(request, _('You cannot edit this closing item.'))
			return redirect('backoffice_daily_closing_item_review', cierre_id=cierre.id, item_id=item.id)
		try:
			actualizar_revision_item_cierre(
				item=item,
				payload={
					'factura_revisada': _parse_bool(request.POST.get('factura_revisada')),
					'pago_verificado': _parse_bool(request.POST.get('pago_verificado')),
					'entrega_confirmada': _parse_bool(request.POST.get('entrega_confirmada')),
					'devolucion_detectada': _parse_bool(request.POST.get('devolucion_detectada')),
					'credit_memo_requerida': _parse_bool(request.POST.get('credit_memo_requerida')),
					'credit_memo_ok': _parse_bool(request.POST.get('credit_memo_ok')),
					'notas': request.POST.get('notas'),
					'exclude': _parse_bool(request.POST.get('exclude')),
					'include_again': _parse_bool(request.POST.get('include_again')),
				},
				usuario=request.user,
			)
			messages.success(request, _('Review saved for %(numero)s.') % {'numero': invoice.numero})
			return redirect('backoffice_daily_closing_detail', cierre_id=cierre.id)
		except ValidationError as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))

	return render(request, 'backoffice/daily_closing_item_review.html', {
		'cierre': cierre,
		'item': item,
		'invoice': invoice,
		'delivery': getattr(invoice, 'delivery', None),
		'notas_credito': notas_credito,
		'can_manage': can_manage,
		'create_note_url': reverse('backoffice_invoice_create_note', args=[invoice.id]),
		'invoice_detail_url': reverse('backoffice_invoice_detail', args=[invoice.id]),
	})


@login_required
@internal_permission_required('backoffice.orders.manage')
@require_POST
def backoffice_daily_closing_release(request, cierre_id):
	cierre = get_object_or_404(CierreDiario, id=cierre_id)
	liberar_todas = _parse_bool(request.POST.get('liberar_todas_listas'))
	item_ids = request.POST.getlist('item_ids')
	try:
		released = liberar_items_cierre(
			cierre=cierre,
			item_ids=item_ids or None,
			usuario=request.user,
			liberar_todas_listas=liberar_todas,
		)
		messages.success(
			request,
			_('%(count)s invoice(s) released to QuickBooks Center.') % {'count': len(released)},
		)
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	return redirect('backoffice_daily_closing_detail', cierre_id=cierre.id)


@login_required
@internal_permission_required('backoffice.orders.manage')
@require_POST
def backoffice_daily_closing_close(request, cierre_id):
	cierre = get_object_or_404(CierreDiario, id=cierre_id)
	try:
		cerrar_cierre_diario(cierre=cierre, usuario=request.user)
		messages.success(request, _('Daily closing was closed.'))
	except ValidationError as exc:
		messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
	return redirect('backoffice_daily_closing_detail', cierre_id=cierre.id)
