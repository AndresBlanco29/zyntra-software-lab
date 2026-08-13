"""Sell-below-cost authorization helpers for sales orders and invoices."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _

from config.core.profit import find_order_lines_sold_below_cost, format_below_cost_error_message

SELL_BELOW_COST_PERMISSION = 'backoffice.pricing.sell_below_cost'


def user_can_authorize_sell_below_cost(user):
	return bool(
		user
		and getattr(user, 'has_internal_permission', None)
		and user.has_internal_permission(SELL_BELOW_COST_PERMISSION)
	)


def parse_sell_below_cost_authorization(data):
	"""Parse authorization fields from request.POST / dict-like data."""
	if data is None:
		return {
			'requested': False,
			'autorizado_por': '',
			'comentario': '',
		}
	confirm_raw = (
		data.get('confirm_sell_below_cost')
		or data.get('autorizar_venta_perdida')
		or ''
	)
	requested = str(confirm_raw).strip().lower() in {'1', 'true', 'yes', 'on'}
	autorizado_por = (data.get('venta_perdida_autorizado_por') or data.get('autorizado_por') or '').strip()
	comentario = (data.get('venta_perdida_comentario') or data.get('comentario') or '').strip()
	return {
		'requested': requested,
		'autorizado_por': autorizado_por,
		'comentario': comentario,
	}


def clear_pedido_sell_below_cost_authorization(pedido, *, save=True):
	pedido.venta_perdida_autorizada = False
	pedido.venta_perdida_autorizado_por = ''
	pedido.venta_perdida_autorizada_por_user = None
	pedido.venta_perdida_comentario = ''
	pedido.venta_perdida_autorizada_en = None
	if save:
		pedido.save(
			update_fields=[
				'venta_perdida_autorizada',
				'venta_perdida_autorizado_por',
				'venta_perdida_autorizada_por_user',
				'venta_perdida_comentario',
				'venta_perdida_autorizada_en',
				'actualizada_en',
			]
		)
	return pedido


def apply_pedido_sell_below_cost_authorization(
	*,
	pedido,
	usuario,
	autorizado_por,
	comentario='',
	below_cost_lines=None,
	save=True,
	audit=True,
):
	autorizado_por = (autorizado_por or '').strip()
	if not autorizado_por:
		raise ValidationError(_('Enter who authorized selling below cost.'))
	if not user_can_authorize_sell_below_cost(usuario):
		raise ValidationError(_('You are not allowed to authorize selling below cost.'))

	now = timezone.now()
	pedido.venta_perdida_autorizada = True
	pedido.venta_perdida_autorizado_por = autorizado_por[:120]
	pedido.venta_perdida_autorizada_por_user = usuario
	pedido.venta_perdida_comentario = (comentario or '').strip()
	pedido.venta_perdida_autorizada_en = now
	if save:
		pedido.save(
			update_fields=[
				'venta_perdida_autorizada',
				'venta_perdida_autorizado_por',
				'venta_perdida_autorizada_por_user',
				'venta_perdida_comentario',
				'venta_perdida_autorizada_en',
				'actualizada_en',
			]
		)

	if audit:
		from config.auditoria.business_events import log_business_event
		from config.auditoria.models import AuditLog

		serializable_lines = []
		for row in below_cost_lines or []:
			serializable_lines.append(
				{
					'item_id': row.get('item_id'),
					'product': row.get('product') or '',
					'presentation': row.get('presentation') or '',
					'quantity': int(row.get('quantity') or 0),
					'cost': str(row.get('cost') or '0'),
					'net_unit_price': str(row.get('net_unit_price') or '0'),
					'unit_profit_amount': str(row.get('unit_profit_amount') or ''),
					'line_profit_amount': str(row.get('line_profit_amount') or ''),
				}
			)

		log_business_event(
			usuario,
			action_label=_('Authorized selling below cost on order #%(id)s') % {'id': pedido.id},
			action_category=AuditLog.CATEGORY_ACTION,
			entity_type='Pedido',
			entity_id=str(pedido.id),
			entity_label=_('Order #%(id)s - %(client)s') % {
				'id': pedido.id,
				'client': pedido.cliente.nombre_empresa,
			},
			metadata={
				'action': 'authorize_sell_below_cost',
				'autorizado_por': autorizado_por,
				'comentario': (comentario or '').strip(),
				'lines': serializable_lines,
			},
			changes=[
				{
					'field': str(_('Sell below cost')),
					'before': str(_('Blocked')),
					'after': str(_('Authorized')),
				},
				{
					'field': str(_('Authorized by')),
					'before': '',
					'after': autorizado_por,
				},
				*([{
					'field': str(_('Comment')),
					'before': '',
					'after': (comentario or '').strip(),
				}] if (comentario or '').strip() else []),
			],
			module='Orders',
		)
	return pedido


def enforce_sell_below_cost_for_pedido(
	*,
	pedido,
	items,
	usuario,
	authorization=None,
	require_existing_authorization=False,
):
	"""Block or authorize below-cost lines on a pedido.

	Returns the list of below-cost lines (empty when none).
	"""
	below_cost_lines = find_order_lines_sold_below_cost(items)
	if not below_cost_lines:
		if getattr(pedido, 'venta_perdida_autorizada', False):
			clear_pedido_sell_below_cost_authorization(pedido, save=True)
		return []

	auth = authorization or {
		'requested': False,
		'autorizado_por': '',
		'comentario': '',
	}

	already_authorized = bool(getattr(pedido, 'venta_perdida_autorizada', False))
	if already_authorized and require_existing_authorization and not auth.get('requested'):
		return below_cost_lines

	if already_authorized and not auth.get('requested'):
		# Keep prior authorization when re-saving without a new override request.
		return below_cost_lines

	if auth.get('requested'):
		apply_pedido_sell_below_cost_authorization(
			pedido=pedido,
			usuario=usuario,
			autorizado_por=auth.get('autorizado_por') or '',
			comentario=auth.get('comentario') or '',
			below_cost_lines=below_cost_lines,
			save=True,
			audit=True,
		)
		return below_cost_lines

	raise ValidationError(format_below_cost_error_message(below_cost_lines))
