"""Persist Take Order carts in the database so reloads do not wipe large drafts."""

from __future__ import annotations

from config.vendedores.models import TakeOrderDraft

SESSION_PEDIDO_NOTA_KEY = 'pedido_nota'


def _normalize_cart(cart):
	if not isinstance(cart, dict):
		return {}
	# JSONField keys must be strings; keep the same shape the session uses.
	normalized = {}
	for key, item in cart.items():
		if not isinstance(item, dict):
			continue
		normalized[str(key)] = item
	return normalized


def _normalize_nota(nota):
	return str(nota or '').strip()


def load_draft_cart(*, vendedor, cliente_id):
	draft = (
		TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=cliente_id)
		.only('cart_data')
		.first()
	)
	if draft is None:
		return {}
	return _normalize_cart(draft.cart_data)


def load_draft_nota(*, vendedor, cliente_id):
	draft = (
		TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=cliente_id)
		.only('nota')
		.first()
	)
	if draft is None:
		return ''
	return _normalize_nota(draft.nota)


def save_draft_cart(*, vendedor, cliente_id, cart, nota=None):
	cliente_id = int(cliente_id)
	cart = _normalize_cart(cart)
	existing = (
		TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=cliente_id)
		.only('id', 'nota')
		.first()
	)
	if nota is None:
		nota_value = _normalize_nota(existing.nota if existing else '')
	else:
		nota_value = _normalize_nota(nota)

	if not cart and not nota_value:
		TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=cliente_id).delete()
		return None

	draft, _created = TakeOrderDraft.objects.update_or_create(
		vendedor=vendedor,
		cliente_id=cliente_id,
		defaults={'cart_data': cart, 'nota': nota_value},
	)
	return draft


def clear_draft_cart(*, vendedor, cliente_id):
	TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=int(cliente_id)).delete()


def get_session_pedido_nota(request):
	return _normalize_nota(request.session.get(SESSION_PEDIDO_NOTA_KEY))


def set_session_pedido_nota(request, nota):
	request.session[SESSION_PEDIDO_NOTA_KEY] = _normalize_nota(nota)
	request.session.modified = True
	return request.session[SESSION_PEDIDO_NOTA_KEY]


def clear_session_pedido_nota(request):
	request.session.pop(SESSION_PEDIDO_NOTA_KEY, None)
	request.session.modified = True


def bind_take_order_cart(request, cliente_id):
	"""
	Attach the correct draft cart for this customer to the session.

	- Switching customers saves the previous draft and loads the new one.
	- Same customer with an empty session restores the DB draft (page reload).
	- Same customer with session data keeps it and re-persists to DB.
	"""
	cliente_id = int(cliente_id)
	prev_id = request.session.get('cliente_id')
	carrito = _normalize_cart(request.session.get('pedido') or {})
	session_nota = get_session_pedido_nota(request)

	if prev_id is not None and int(prev_id) != cliente_id:
		save_draft_cart(
			vendedor=request.user,
			cliente_id=int(prev_id),
			cart=carrito,
			nota=session_nota,
		)
		carrito = load_draft_cart(vendedor=request.user, cliente_id=cliente_id)
		session_nota = load_draft_nota(vendedor=request.user, cliente_id=cliente_id)
	elif not carrito:
		carrito = load_draft_cart(vendedor=request.user, cliente_id=cliente_id)
		if not session_nota:
			session_nota = load_draft_nota(vendedor=request.user, cliente_id=cliente_id)
		elif carrito or session_nota:
			save_draft_cart(
				vendedor=request.user,
				cliente_id=cliente_id,
				cart=carrito,
				nota=session_nota,
			)
	else:
		save_draft_cart(
			vendedor=request.user,
			cliente_id=cliente_id,
			cart=carrito,
			nota=session_nota,
		)

	request.session['pedido'] = carrito
	request.session['cliente_id'] = cliente_id
	set_session_pedido_nota(request, session_nota)
	request.session.modified = True
	return carrito


def persist_session_take_order_cart(request):
	cliente_id = request.session.get('cliente_id')
	if not cliente_id:
		return None
	carrito = _normalize_cart(request.session.get('pedido') or {})
	request.session['pedido'] = carrito
	request.session.modified = True
	return save_draft_cart(
		vendedor=request.user,
		cliente_id=cliente_id,
		cart=carrito,
		nota=get_session_pedido_nota(request),
	)


def persist_session_pedido_nota(request, nota):
	cliente_id = request.session.get('cliente_id')
	nota_value = set_session_pedido_nota(request, nota)
	if not cliente_id:
		return nota_value
	carrito = _normalize_cart(request.session.get('pedido') or {})
	save_draft_cart(
		vendedor=request.user,
		cliente_id=cliente_id,
		cart=carrito,
		nota=nota_value,
	)
	return nota_value


def draft_item_counts_for_clientes(*, vendedor, cliente_ids):
	"""Return {cliente_id: total_qty} for drafts belonging to this vendor."""
	if not cliente_ids:
		return {}
	rows = TakeOrderDraft.objects.filter(
		vendedor=vendedor,
		cliente_id__in=cliente_ids,
	).only('cliente_id', 'cart_data')
	counts = {}
	for draft in rows:
		qty = sum(int(item.get('cantidad') or 0) for item in (draft.cart_data or {}).values())
		if qty > 0:
			counts[draft.cliente_id] = qty
	return counts
