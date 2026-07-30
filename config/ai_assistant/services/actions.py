from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from config.ai_assistant.models import AssistantPendingAction
from config.productos.models import Presentacion
from config.productos.promotions import reaplicar_promociones_en_lineas_sesion


ACTION_ADD_CART = 'ADD_CART_ITEM'


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

    if action.action_type != ACTION_ADD_CART:
        raise ValidationError('Unsupported assistant action.')
    presentacion = Presentacion.objects.select_related('producto').filter(
        id=action.payload.get('presentation_id'),
        producto__activo=True,
    ).first()
    if presentacion is None:
        raise ValidationError('The selected product is no longer available.')
    quantity = max(min(int(action.payload.get('quantity') or 1), 999), 1)
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
        cliente=get_customer_for_user(request.user),
    )
    request.session['carrito'] = carrito
    request.session.modified = True
    action.status = AssistantPendingAction.STATUS_CONFIRMED
    action.confirmed_at = timezone.now()
    action.save(update_fields=['status', 'confirmed_at'])
    return {
        'message': 'Product added to your order. Review it before sending your request.',
        'cart_items': sum(int(item.get('cantidad') or 0) for item in carrito.values()),
    }
