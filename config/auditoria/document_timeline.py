"""Document-level audit timelines for orders and invoices."""

from __future__ import annotations

from django.db.models import Q

from config.auditoria.models import AuditLog


def get_document_audit_timeline(*, pairs, limit=80):
	"""
	Return recent AuditLog rows for one or more (entity_type, entity_id) pairs.

	``pairs`` example: [('Pedido', '12'), ('Invoice', '5')]
	"""
	query = Q()
	valid_pairs = []
	for entity_type, entity_id in pairs or []:
		etype = (entity_type or '').strip()
		eid = str(entity_id or '').strip()
		if not etype or not eid:
			continue
		valid_pairs.append((etype, eid))
		query |= Q(entity_type=etype, entity_id=eid)

	if not valid_pairs:
		return []

	return list(
		AuditLog.objects.filter(query)
		.select_related('actor')
		.order_by('-created_at', '-id')[:limit]
	)


def get_pedido_audit_timeline(pedido, *, limit=80):
	pairs = [('Pedido', str(pedido.id))]
	invoice = getattr(pedido, 'invoice', None)
	if invoice is not None:
		pairs.append(('Invoice', str(invoice.id)))
	return get_document_audit_timeline(pairs=pairs, limit=limit)


def get_invoice_audit_timeline(invoice, *, limit=80):
	pairs = [('Invoice', str(invoice.id))]
	if invoice.pedido_id:
		pairs.append(('Pedido', str(invoice.pedido_id)))
	return get_document_audit_timeline(pairs=pairs, limit=limit)
