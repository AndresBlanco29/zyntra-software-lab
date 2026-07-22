"""Daily Closing (Cierre Diario) — review gate before QuickBooks export."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import CierreDiario, CierreDiarioItem, Invoice, NotaAjuste


TERMINAL_DELIVERY_STATES = {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}


def invoices_elegibles_para_cierre(*, search=''):
	"""
	Invoices the user can manually add to a daily closing:
	- not voided
	- not yet synced to QuickBooks
	- not already released
	- not already in an open closing
	"""
	in_open_closing_ids = CierreDiarioItem.objects.filter(
		cierre__estado__in={'ABIERTO', 'EN_REVISION', 'LISTO'},
	).values_list('invoice_id', flat=True)
	qs = (
		Invoice.objects.filter(estado='GENERADA', quickbooks_id__isnull=True, cierre_liberada=False)
		.exclude(id__in=in_open_closing_ids)
		.select_related('cliente', 'delivery', 'driver')
		.order_by('-id')
	)

	term = (search or '').strip()
	if term:
		qs = qs.filter(
			Q(numero__icontains=term)
			| Q(id__icontains=term)
			| Q(cliente__nombre_empresa__icontains=term)
		)
	return qs


def build_invoice_closing_alerts(invoice):
	alerts = []
	delivery = getattr(invoice, 'delivery', None)
	if invoice.estado == 'ANULADA':
		alerts.append({'code': 'VOIDED', 'level': 'block', 'message': str(_('Invoice is voided.'))})
	if not invoice.items.filter(cantidad_facturada__gt=0).exists():
		alerts.append({'code': 'NO_LINES', 'level': 'block', 'message': str(_('Invoice has no billable lines.'))})
	if delivery is None:
		alerts.append({'code': 'NO_DELIVERY', 'level': 'warn', 'message': str(_('No delivery record yet.'))})
	elif delivery.estado not in TERMINAL_DELIVERY_STATES:
		alerts.append({
			'code': 'DELIVERY_OPEN',
			'level': 'warn',
			'message': str(_('Delivery is not completed yet (%(status)s).') % {
				'status': delivery.get_estado_display(),
			}),
		})
	if delivery and delivery.estado_pago == 'NO_PAGADO':
		alerts.append({
			'code': 'UNPAID',
			'level': 'warn',
			'message': str(_('Delivery marked as unpaid.')),
		})
	draft_credits = NotaAjuste.objects.filter(
		invoice=invoice,
		tipo_documento='CREDITO',
		estado='BORRADOR',
	).count()
	if draft_credits:
		alerts.append({
			'code': 'DRAFT_CREDIT',
			'level': 'warn',
			'message': str(_('There are draft credit notes linked to this invoice.')),
		})
	return alerts


def _has_blocking_alerts(alerts):
	return any(alert.get('level') == 'block' for alert in (alerts or []))


def evaluate_item_readiness(item):
	"""Update lista_para_exportar / estado from checklist + alerts (except LIBERADA/EXCLUIDA)."""
	if item.estado in {'LIBERADA', 'EXCLUIDA'}:
		return item

	alerts = list(item.alertas or [])
	blocked = _has_blocking_alerts(alerts)
	credit_ok = (not item.credit_memo_requerida) or item.credit_memo_ok
	ready = (
		not blocked
		and item.factura_revisada
		and item.pago_verificado
		and item.entrega_confirmada
		and credit_ok
	)
	item.lista_para_exportar = ready
	if blocked:
		item.estado = 'BLOQUEADA'
	elif ready:
		item.estado = 'LISTA'
	elif item.factura_revisada or item.pago_verificado or item.entrega_confirmada:
		item.estado = 'EN_REVISION'
	else:
		item.estado = 'PENDIENTE'
	return item


def recalcular_totales_cierre(cierre):
	items = list(
		cierre.items.select_related('invoice', 'invoice__delivery').all()
	)
	monto_total = Decimal('0.00')
	monto_pagado = Decimal('0.00')
	balance = Decimal('0.00')
	creditos = Decimal('0.00')
	listos = 0
	bloqueados = 0
	liberados = 0

	for item in items:
		invoice = item.invoice
		monto_total += invoice.total_neto or Decimal('0.00')
		balance += invoice.saldo_cliente or Decimal('0.00')
		creditos += invoice.total_creditos or Decimal('0.00')
		delivery = getattr(invoice, 'delivery', None)
		if delivery and delivery.estado_pago == 'PAGADO':
			monto_pagado += getattr(delivery, 'monto_pagado', None) or invoice.total_neto or Decimal('0.00')
		if item.estado == 'LISTA':
			listos += 1
		elif item.estado == 'BLOQUEADA':
			bloqueados += 1
		elif item.estado == 'LIBERADA':
			liberados += 1

	cierre.total_documentos = len(items)
	cierre.total_invoices = len(items)
	cierre.monto_total = monto_total
	cierre.monto_pagado = monto_pagado
	cierre.balance_abierto = balance
	cierre.total_creditos = creditos
	cierre.items_listos = listos
	cierre.items_bloqueados = bloqueados
	cierre.items_liberados = liberados

	if cierre.estado != 'CERRADO':
		active = [i for i in items if i.estado not in {'EXCLUIDA', 'LIBERADA'}]
		if not items:
			cierre.estado = 'ABIERTO'
		elif active and all(i.estado == 'LISTA' for i in active):
			cierre.estado = 'LISTO'
		elif any(i.estado in {'EN_REVISION', 'LISTA', 'BLOQUEADA'} for i in items):
			cierre.estado = 'EN_REVISION'
		else:
			cierre.estado = 'ABIERTO'

	cierre.save(
		update_fields=[
			'total_documentos',
			'total_invoices',
			'monto_total',
			'monto_pagado',
			'balance_abierto',
			'total_creditos',
			'items_listos',
			'items_bloqueados',
			'items_liberados',
			'estado',
			'actualizado_en',
		]
	)
	return cierre


@transaction.atomic
def crear_cierre_diario(*, fecha, usuario=None, notas=''):
	return CierreDiario.objects.create(
		fecha=fecha,
		estado='ABIERTO',
		notas=(notas or '').strip(),
		creado_por=usuario,
	)


@transaction.atomic
def agregar_invoices_al_cierre(*, cierre, invoice_ids, usuario=None):
	if not cierre.is_editable:
		raise ValidationError(_('This daily closing is closed and cannot be edited.'))
	ids = []
	seen = set()
	for raw in invoice_ids or []:
		try:
			value = int(raw)
		except (TypeError, ValueError):
			continue
		if value not in seen:
			seen.add(value)
			ids.append(value)
	if not ids:
		raise ValidationError(_('Select at least one invoice to add to the daily closing.'))

	elegibles = {
		invoice.id: invoice
		for invoice in invoices_elegibles_para_cierre().filter(id__in=ids)
	}
	missing = [invoice_id for invoice_id in ids if invoice_id not in elegibles]
	if missing:
		raise ValidationError(
			_('Some selected invoices cannot be added (already synced, released, voided, or in another open closing).')
		)

	created = []
	for invoice_id in ids:
		invoice = elegibles[invoice_id]
		alerts = build_invoice_closing_alerts(invoice)
		item = CierreDiarioItem(
			cierre=cierre,
			invoice=invoice,
			alertas=alerts,
			estado='PENDIENTE',
		)
		# Soft pre-check delivery completed → suggest entrega_confirmada unchecked still
		evaluate_item_readiness(item)
		created.append(item)
	CierreDiarioItem.objects.bulk_create(created)
	recalcular_totales_cierre(cierre)
	return created


@transaction.atomic
def actualizar_revision_item_cierre(*, item, payload, usuario=None):
	cierre = item.cierre
	if not cierre.is_editable:
		raise ValidationError(_('This daily closing is closed and cannot be edited.'))
	if item.estado == 'LIBERADA':
		raise ValidationError(_('Released invoices cannot be edited in the closing.'))

	if payload.get('exclude'):
		item.estado = 'EXCLUIDA'
		item.lista_para_exportar = False
		item.notas = (payload.get('notas') or item.notas or '').strip()
		item.revisado_por = usuario
		item.revisado_en = timezone.now()
		item.save()
		recalcular_totales_cierre(cierre)
		return item

	if payload.get('include_again') and item.estado == 'EXCLUIDA':
		item.estado = 'PENDIENTE'

	item.factura_revisada = bool(payload.get('factura_revisada'))
	item.pago_verificado = bool(payload.get('pago_verificado'))
	item.entrega_confirmada = bool(payload.get('entrega_confirmada'))
	item.devolucion_detectada = bool(payload.get('devolucion_detectada'))
	item.credit_memo_requerida = bool(payload.get('credit_memo_requerida'))
	item.credit_memo_ok = bool(payload.get('credit_memo_ok'))
	item.notas = (payload.get('notas') or '').strip()
	item.alertas = build_invoice_closing_alerts(item.invoice)
	item.revisado_por = usuario
	item.revisado_en = timezone.now()
	evaluate_item_readiness(item)
	item.save()
	recalcular_totales_cierre(cierre)
	return item


@transaction.atomic
def liberar_items_cierre(*, cierre, item_ids=None, usuario=None, liberar_todas_listas=False):
	if not cierre.is_editable:
		raise ValidationError(_('This daily closing is closed and cannot be edited.'))

	qs = cierre.items.select_related('invoice').select_for_update()
	if liberar_todas_listas:
		qs = qs.filter(estado='LISTA', lista_para_exportar=True)
	elif item_ids:
		qs = qs.filter(id__in=item_ids, estado='LISTA', lista_para_exportar=True)
	else:
		raise ValidationError(_('Select ready invoices to release, or release all ready ones.'))

	items = list(qs)
	if not items:
		raise ValidationError(_('No ready invoices available to release.'))

	now = timezone.now()
	released_invoice_ids = []
	for item in items:
		invoice = item.invoice
		if invoice.quickbooks_id or invoice.estado == 'ANULADA':
			raise ValidationError(
				_('Invoice %(numero)s cannot be released.') % {'numero': invoice.numero}
			)
		invoice.cierre_liberada = True
		invoice.cierre_liberada_en = now
		invoice.cierre_liberada_por = usuario
		invoice.save(update_fields=['cierre_liberada', 'cierre_liberada_en', 'cierre_liberada_por', 'actualizada_en'])
		item.estado = 'LIBERADA'
		item.liberado_en = now
		item.save(update_fields=['estado', 'liberado_en', 'actualizado_en'])
		released_invoice_ids.append(invoice.id)

	recalcular_totales_cierre(cierre)

	from config.auditoria.business_events import log_business_event
	from config.auditoria.models import AuditLog

	for invoice_id in released_invoice_ids:
		log_business_event(
			usuario,
			action_label=_('Released invoice #%(id)s for QuickBooks export') % {'id': invoice_id},
			action_category=AuditLog.CATEGORY_ACTION,
			entity_type='Invoice',
			entity_id=str(invoice_id),
			entity_label=_('Invoice #%(id)s') % {'id': invoice_id},
			metadata={
				'cierre_id': cierre.id,
				'cierre_fecha': str(cierre.fecha),
				'action': 'daily_closing_release',
			},
			module='Invoices',
		)

	return items


@transaction.atomic
def cerrar_cierre_diario(*, cierre, usuario=None):
	if cierre.estado == 'CERRADO':
		return cierre
	pending = cierre.items.exclude(estado__in={'LIBERADA', 'EXCLUIDA'}).exists()
	if pending:
		raise ValidationError(
			_('Release or exclude all invoices before closing this daily closing.')
		)
	cierre.estado = 'CERRADO'
	cierre.cerrado_por = usuario
	cierre.cerrado_en = timezone.now()
	cierre.save(update_fields=['estado', 'cerrado_por', 'cerrado_en', 'actualizado_en'])

	from config.auditoria.business_events import log_business_event
	from config.auditoria.models import AuditLog

	log_business_event(
		usuario,
		action_label=_('Closed daily closing for %(fecha)s') % {'fecha': cierre.fecha},
		action_category=AuditLog.CATEGORY_ACTION,
		entity_type='CierreDiario',
		entity_id=str(cierre.id),
		entity_label=_('Daily closing %(fecha)s') % {'fecha': cierre.fecha},
		metadata={'action': 'daily_closing_close', 'fecha': str(cierre.fecha)},
		module='Invoices',
	)
	return cierre


def invoice_puede_exportarse_a_quickbooks(invoice):
	return (
		invoice is not None
		and invoice.estado == 'GENERADA'
		and not invoice.quickbooks_id
		and bool(invoice.cierre_liberada)
	)
