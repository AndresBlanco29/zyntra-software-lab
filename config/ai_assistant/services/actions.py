from datetime import timedelta

from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.utils import timezone

from config.ai_assistant.models import AssistantPendingAction
from config.productos.models import Presentacion
from config.productos.promotions import reaplicar_promociones_en_lineas_sesion


ACTION_ADD_CART = 'ADD_CART_ITEM'
ACTION_UPDATE_CART = 'UPDATE_CART_ITEM'
ACTION_REMOVE_CART = 'REMOVE_CART_ITEM'
ACTION_REORDER = 'REORDER'
ACTION_SUBMIT_QUOTE_REQUEST = 'SUBMIT_QUOTE_REQUEST'
ACTION_ACCEPT_QUOTE = 'ACCEPT_QUOTE'
ACTION_CANCEL_QUOTE = 'CANCEL_QUOTE'
ACTION_CLEAR_CART = 'CLEAR_CART'


def create_pending_action(*, request, action_type, payload):
    from config.ai_assistant.services.identity import get_customer_for_user, get_visitor_id

    return AssistantPendingAction.objects.create(
        visitor_id=get_visitor_id(request),
        user=request.user if request.user.is_authenticated else None,
        cliente=get_customer_for_user(request.user),
        action_type=action_type,
        payload=payload,
        expires_at=timezone.now() + timedelta(minutes=10),
    )


def execute_confirmed_action(*, request, action):
    from config.ai_assistant.services.identity import get_customer_for_user, get_visitor_id

    if action.status != AssistantPendingAction.STATUS_PENDING or action.expires_at <= timezone.now():
        action.status = AssistantPendingAction.STATUS_EXPIRED
        action.save(update_fields=['status'])
        raise ValidationError('This action is no longer available.')
    if action.visitor_id != get_visitor_id(request):
        raise ValidationError('This confirmation does not belong to this visitor.')
    if action.user_id and action.user_id != request.user.id:
        raise ValidationError('This confirmation does not belong to this user.')
    cliente = get_customer_for_user(request.user)
    if action.action_type != ACTION_ADD_CART and (cliente is None or action.cliente_id != cliente.id):
        raise ValidationError('Customer login is required for this action.')
    if action.action_type == ACTION_ADD_CART:
        result = _add_cart_item(request, action.payload, cliente)
    elif action.action_type == ACTION_UPDATE_CART:
        result = _update_cart_item(request, action.payload, cliente)
    elif action.action_type == ACTION_REMOVE_CART:
        result = _remove_cart_item(request, action.payload, cliente)
    elif action.action_type == ACTION_CLEAR_CART:
        result = _clear_cart(request)
    elif action.action_type == ACTION_REORDER:
        result = _load_reorder(request, action.payload, cliente)
    elif action.action_type == ACTION_SUBMIT_QUOTE_REQUEST:
        result = _submit_quote_request(request)
    elif action.action_type in {ACTION_ACCEPT_QUOTE, ACTION_CANCEL_QUOTE}:
        result = _decide_quote(request, action.payload, action.action_type == ACTION_ACCEPT_QUOTE)
    else:
        raise ValidationError('Unsupported assistant action.')
    action.status = AssistantPendingAction.STATUS_CONFIRMED
    action.confirmed_at = timezone.now()
    action.save(update_fields=['status', 'confirmed_at'])
    return result


def _add_cart_item(request, payload, cliente):
    presentacion = Presentacion.objects.select_related('producto').filter(
        id=payload.get('presentation_id'),
        producto__activo=True,
    ).first()
    if presentacion is None:
        raise ValidationError('The selected product is no longer available.')
    quantity = max(min(int(payload.get('quantity') or 1), 999), 1)
    carrito = request.session.get('carrito', {})
    key = str(presentacion.id)
    if key in carrito:
        carrito[key]['cantidad'] = int(carrito[key].get('cantidad') or 0) + quantity
    else:
        # Current pricing/promotion logic runs on the server; price is recalculated
        # by the existing cart screen before the customer can submit the request.
        carrito[key] = {
            'producto_id': presentacion.producto_id,
            'presentacion_id': presentacion.id,
            'nombre': presentacion.producto.nombre,
            'cantidad': quantity,
            'precio': 0,
        }
    reaplicar_promociones_en_lineas_sesion(
        carrito,
        cliente=cliente,
    )
    request.session['carrito'] = carrito
    request.session.modified = True
    return {
        'message': 'Product added to your order. Review it before sending your request.',
        'cart_items': sum(int(item.get('cantidad') or 0) for item in carrito.values()),
    }


def _clear_cart(request):
    request.session['carrito'] = {}
    request.session.modified = True
    return {'message': 'Your order was cleared.', 'cart_items': 0}


def _update_cart_item(request, payload, cliente):
    key = str(payload.get('cart_key') or '')
    carrito = request.session.get('carrito', {})
    item = carrito.get(key)
    if not isinstance(item, dict) or key.startswith('gift:'):
        raise ValidationError('This order line can no longer be changed.')
    quantity = max(min(int(payload.get('quantity') or 1), 999), 1)
    item['cantidad'] = quantity
    reaplicar_promociones_en_lineas_sesion(carrito, cliente=cliente)
    request.session['carrito'] = carrito
    request.session.modified = True
    return {'message': 'Order quantity updated. Review your order before sending it.', 'cart_items': sum(int(row.get('cantidad') or 0) for row in carrito.values())}


def _remove_cart_item(request, payload, cliente):
    key = str(payload.get('cart_key') or '')
    carrito = request.session.get('carrito', {})
    if key not in carrito or key.startswith('gift:'):
        raise ValidationError('This order line can no longer be removed.')
    carrito.pop(key)
    reaplicar_promociones_en_lineas_sesion(carrito, cliente=cliente)
    request.session['carrito'] = carrito
    request.session.modified = True
    return {'message': 'Product removed from your order.', 'cart_items': sum(int(row.get('cantidad') or 0) for row in carrito.values())}


def _load_reorder(request, payload, cliente):
    from config.cotizaciones.views import _quote_item_price_for_customer
    from config.pedidos.client_history import merge_pedido_into_session_cart
    from config.pedidos.models import Pedido
    from config.productos.promotions import aplicar_promocion_en_item_sesion

    pedido = Pedido.objects.filter(pk=payload.get('pedido_id'), cliente=cliente).first()
    if pedido is None:
        raise ValidationError('Order not found.')
    carrito, added_count = merge_pedido_into_session_cart(
        carrito=request.session.get('carrito', {}) or {},
        pedido=pedido,
        price_fn=lambda *, presentacion: _quote_item_price_for_customer(cliente=cliente, presentacion=presentacion, session_price=0),
        promo_fn=lambda item, **kwargs: aplicar_promocion_en_item_sesion(item, cliente=cliente, **kwargs),
    )
    request.session['carrito'] = carrito
    request.session.modified = True
    return {'message': 'Previous order loaded into My Order. Review it before sending your request.', 'added_items': added_count}


def _submit_quote_request(request):
    """Delegate to the established portal view so pricing, promos and alerts remain canonical."""
    from config.ai_assistant.services.identity import get_customer_for_user
    from config.cotizaciones.models import Cotizacion
    from config.cotizaciones.views import guardar_cotizacion

    cliente = get_customer_for_user(request.user)
    if not (request.session.get('carrito') or {}):
        raise ValidationError('Your order is empty.')
    before_ids = set(Cotizacion.objects.filter(cliente=cliente).values_list('id', flat=True))
    original_post = request.POST
    try:
        request.POST = QueryDict('', mutable=True)
        guardar_cotizacion(request)
    finally:
        request.POST = original_post
    created = Cotizacion.objects.filter(cliente=cliente).exclude(id__in=before_ids).filter(estado='ENVIADA').exists()
    if not created:
        raise ValidationError('The quote request could not be sent. Review My Order and try again.')
    return {'message': 'Your quote request was sent for review.'}


def _decide_quote(request, payload, approve):
    from config.ai_assistant.services.identity import get_customer_for_user
    from config.cotizaciones.models import Cotizacion
    from config.cotizaciones.views import cliente_cotizacion_recibida_detalle

    cliente = get_customer_for_user(request.user)
    quote = Cotizacion.objects.filter(token_cliente=payload.get('quote_token'), cliente=cliente).first()
    if quote is None:
        raise ValidationError('Quote not found.')
    original_post = request.POST
    try:
        post = QueryDict('', mutable=True)
        post['accion'] = 'aprobar' if approve else 'cancelar'
        if approve:
            post['acepta_terminos'] = 'on'
        post['nota_cliente'] = str(payload.get('note') or '')[:1000]
        request.POST = post
        cliente_cotizacion_recibida_detalle(request, quote.token_cliente)
    finally:
        request.POST = original_post
    quote.refresh_from_db()
    expected_status = 'CONFIRMADA_CLIENTE' if approve else 'CANCELADA_CLIENTE'
    if quote.estado != expected_status:
        raise ValidationError('The quote could not be updated. Review it and try again.')
    return {'message': 'Quote confirmed and sent.' if approve else 'Quote cancelled.'}
