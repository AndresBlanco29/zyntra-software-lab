from django.urls import reverse

from config.ai_assistant.models import AssistantDomainEvent
from config.ai_assistant.services.identity import get_customer_for_user
from config.cotizaciones.models import Cotizacion
from config.pedidos.client_history import list_cliente_favorite_product_ids, list_cliente_purchase_orders


def build_customer_context(request):
    """Small, scoped context. Volatile commerce values are queried by tools."""
    user = request.user
    cliente = get_customer_for_user(user)
    context = {
        'authenticated': bool(getattr(user, 'is_authenticated', False)),
        'role': getattr(user, 'role', '') if getattr(user, 'is_authenticated', False) else '',
        'page': str(request.GET.get('ai_page') or request.path)[:80],
        'actions': [],
    }
    if cliente is None:
        context['next_recommended_action'] = {
            'label': 'Registrarme',
            'url': reverse('registro_usuario'),
            'tour_id': 'registration',
        }
        return context

    context.update({
        'customer': {
            'first_name': (user.first_name or '').strip(),
            'company': cliente.nombre_empresa,
            'approved': bool(cliente.aprobado),
            'review_status': cliente.estado_revision,
            'payment_terms': cliente.get_terminos_pago_label(),
        },
        'actions': [
            {'label': 'Ver catálogo', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
            {'label': 'Mi orden', 'url': reverse('ver_cotizacion')},
            {'label': 'Mis cotizaciones', 'url': reverse('cliente_cotizaciones_recibidas')},
            {'label': 'Mis pedidos', 'url': reverse('cliente_historial_ordenes'), 'tour_id': 'reorder'},
        ],
    })
    cart = request.session.get('carrito', {}) or {}
    cart_lines = [
        {
            'presentation_id': item.get('presentacion_id'),
            'name': str(item.get('nombre') or '')[:120],
            'quantity': int(item.get('cantidad') or 0),
        }
        for item in cart.values()
        if isinstance(item, dict)
    ]
    ready_quotes = list(
        Cotizacion.objects.filter(cliente=cliente, estado='LISTA_PARA_CONFIRMACION')
        .order_by('-fecha')
        .values('id', 'token_cliente', 'total')[:3]
    )
    latest_orders = list(
        list_cliente_purchase_orders(cliente=cliente).values('id', 'estado', 'creada_en')[:3]
    )
    context.update({
        'cart': {'line_count': len(cart_lines), 'items': cart_lines[:12]},
        'quotes_ready': [
            {'id': quote['id'], 'total': str(quote['total']), 'url': reverse('cliente_cotizacion_recibida_detalle', args=[quote['token_cliente']])}
            for quote in ready_quotes
        ],
        'recent_orders': [
            {'id': order['id'], 'status': order['estado']}
            for order in latest_orders
        ],
        'favorite_products': list_cliente_favorite_product_ids(cliente=cliente, limit=5),
    })
    latest_event = (
        AssistantDomainEvent.objects.filter(cliente=cliente, consumed_at__isnull=True)
        .order_by('-created_at')
        .first()
    )
    if latest_event:
        context['pending_event'] = {
            'type': latest_event.event_type,
            'entity_type': latest_event.entity_type,
            'entity_id': latest_event.entity_id,
            'payload': latest_event.payload,
        }
    return context
