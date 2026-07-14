"""Persist Take Order / Create Quote carts in the database so reloads do not wipe large drafts."""

from __future__ import annotations

from config.vendedores.models import TakeOrderDraft

SESSION_PEDIDO_NOTA_KEY = 'pedido_nota'
SESSION_TAKE_ORDER_FLOW_KEY = 'take_order_flow'
FLOW_ORDER = TakeOrderDraft.FLOW_ORDER
FLOW_QUOTE = TakeOrderDraft.FLOW_QUOTE


def normalize_take_order_flow(flow):
	return FLOW_QUOTE if str(flow or '').strip().lower() == FLOW_QUOTE else FLOW_ORDER


def get_take_order_flow(request):
	return normalize_take_order_flow(request.session.get(SESSION_TAKE_ORDER_FLOW_KEY, FLOW_ORDER))


def set_take_order_flow(request, flow):
	"""Switch between order and quote carts without mixing draft data."""
	flow = normalize_take_order_flow(flow)
	previous = get_take_order_flow(request)
	cliente_id = request.session.get('cliente_id')

	if previous != flow:
		if cliente_id:
			save_draft_cart(
				vendedor=request.user,
				cliente_id=int(cliente_id),
				cart=_normalize_cart(request.session.get('pedido') or {}),
				nota=get_session_pedido_nota(request),
				flow=previous,
			)
		request.session[SESSION_TAKE_ORDER_FLOW_KEY] = flow
		request.session['pedido'] = {}
		clear_session_pedido_nota(request)
		request.session.modified = True
		if cliente_id:
			bind_take_order_cart(request, cliente_id)
		return flow

	request.session[SESSION_TAKE_ORDER_FLOW_KEY] = flow
	request.session.modified = True
	return flow


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
	# Keep spaces while drafting so typing multi-word comments does not fight the UI.
	return str(nota or '')


def _nota_is_empty(nota):
	return not str(nota or '').strip()


def load_draft_cart(*, vendedor, cliente_id, flow=FLOW_ORDER):
	flow = normalize_take_order_flow(flow)
	draft = (
		TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=cliente_id, flow=flow)
		.only('cart_data')
		.first()
	)
	if draft is None:
		return {}
	return _normalize_cart(draft.cart_data)


def load_draft_nota(*, vendedor, cliente_id, flow=FLOW_ORDER):
	flow = normalize_take_order_flow(flow)
	draft = (
		TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=cliente_id, flow=flow)
		.only('nota')
		.first()
	)
	if draft is None:
		return ''
	return _normalize_nota(draft.nota)


def save_draft_cart(*, vendedor, cliente_id, cart, nota=None, flow=FLOW_ORDER):
	cliente_id = int(cliente_id)
	flow = normalize_take_order_flow(flow)
	cart = _normalize_cart(cart)
	existing = (
		TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=cliente_id, flow=flow)
		.only('id', 'nota')
		.first()
	)
	if nota is None:
		nota_value = _normalize_nota(existing.nota if existing else '')
	else:
		nota_value = _normalize_nota(nota)

	if not cart and _nota_is_empty(nota_value):
		TakeOrderDraft.objects.filter(vendedor=vendedor, cliente_id=cliente_id, flow=flow).delete()
		return None

	draft, _created = TakeOrderDraft.objects.update_or_create(
		vendedor=vendedor,
		cliente_id=cliente_id,
		flow=flow,
		defaults={'cart_data': cart, 'nota': nota_value},
	)
	return draft


def clear_draft_cart(*, vendedor, cliente_id, flow=FLOW_ORDER):
	TakeOrderDraft.objects.filter(
		vendedor=vendedor,
		cliente_id=int(cliente_id),
		flow=normalize_take_order_flow(flow),
	).delete()


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
	Attach the correct draft cart for this customer + flow to the session.

	- Switching customers saves the previous draft and loads the new one.
	- Same customer with an empty session restores the DB draft (page reload).
	- Same customer with session data keeps it and re-persists to DB.
	"""
	cliente_id = int(cliente_id)
	flow = get_take_order_flow(request)
	prev_id = request.session.get('cliente_id')
	carrito = _normalize_cart(request.session.get('pedido') or {})
	session_nota = get_session_pedido_nota(request)

	if prev_id is not None and int(prev_id) != cliente_id:
		save_draft_cart(
			vendedor=request.user,
			cliente_id=int(prev_id),
			cart=carrito,
			nota=session_nota,
			flow=flow,
		)
		carrito = load_draft_cart(vendedor=request.user, cliente_id=cliente_id, flow=flow)
		session_nota = load_draft_nota(vendedor=request.user, cliente_id=cliente_id, flow=flow)
	elif not carrito:
		carrito = load_draft_cart(vendedor=request.user, cliente_id=cliente_id, flow=flow)
		if not session_nota:
			session_nota = load_draft_nota(vendedor=request.user, cliente_id=cliente_id, flow=flow)
		elif carrito or session_nota:
			save_draft_cart(
				vendedor=request.user,
				cliente_id=cliente_id,
				cart=carrito,
				nota=session_nota,
				flow=flow,
			)
	else:
		save_draft_cart(
			vendedor=request.user,
			cliente_id=cliente_id,
			cart=carrito,
			nota=session_nota,
			flow=flow,
		)

	request.session['pedido'] = carrito
	request.session['cliente_id'] = cliente_id
	request.session[SESSION_TAKE_ORDER_FLOW_KEY] = flow
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
		flow=get_take_order_flow(request),
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
		flow=get_take_order_flow(request),
	)
	return nota_value


def draft_item_counts_for_clientes(*, vendedor, cliente_ids, flow=FLOW_ORDER):
	"""Return {cliente_id: total_qty} for drafts belonging to this vendor + flow."""
	if not cliente_ids:
		return {}
	flow = normalize_take_order_flow(flow)
	rows = TakeOrderDraft.objects.filter(
		vendedor=vendedor,
		cliente_id__in=cliente_ids,
		flow=flow,
	).only('cliente_id', 'cart_data')
	counts = {}
	for draft in rows:
		qty = sum(int(item.get('cantidad') or 0) for item in (draft.cart_data or {}).values())
		if qty > 0:
			counts[draft.cliente_id] = qty
	return counts
