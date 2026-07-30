from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from config.ai_assistant.models import AssistantDomainEvent, AssistantUserState
from config.ai_assistant.services.identity import get_customer_for_user, get_visitor_id, get_visitor_profile
from config.cotizaciones.models import Cotizacion
from config.pedidos.client_history import list_cliente_favorite_product_ids, list_cliente_purchase_orders


def build_customer_context(request):
    """Small, scoped context. Volatile commerce values are queried by tools."""
    user = request.user
    cliente = get_customer_for_user(user)
    visitor_profile, _ = get_visitor_profile(request)
    context = {
        'authenticated': bool(getattr(user, 'is_authenticated', False)),
        'role': getattr(user, 'role', '') if getattr(user, 'is_authenticated', False) else '',
        'page': str(request.GET.get('ai_page') or request.path)[:80],
        'actions': [],
    }
    if cliente is None:
        linked_customer = visitor_profile.cliente
        if linked_customer and visitor_profile.last_seen_at >= timezone.now() - timedelta(days=30):
            latest_event = AssistantDomainEvent.objects.filter(
                cliente=linked_customer,
                event_type__in=[
                    AssistantDomainEvent.TYPE_ACCOUNT_APPROVED,
                    AssistantDomainEvent.TYPE_ACCOUNT_NEEDS_CORRECTION,
                ],
                consumed_at__isnull=True,
            ).order_by('-created_at').first()
            if latest_event:
                context['pending_event'] = {
                    'id': latest_event.id,
                    'type': latest_event.event_type,
                    'entity_type': latest_event.entity_type,
                    'entity_id': latest_event.entity_id,
                    'payload': latest_event.payload,
                }
        context['next_recommended_action'] = {
            'label': 'Iniciar registro guiado',
            'url': reverse('home'),
            'tour_id': 'registration',
        }
        if visitor_profile.first_visit_prompted_at is None:
            context['proactive'] = {
                'kind': 'first_visit',
                'message': (
                    '¡Hola! 👋\n\nBienvenido a La Tortilla Grocery.\n\n'
                    'Soy Paco, tu asistente virtual. Veo que es tu primera visita. ¿En qué puedo ayudarte hoy?'
                ),
                'actions': [
                    {'label': 'Registrarme como cliente', 'url': reverse('home'), 'tour_id': 'registration'},
                    {'label': 'Ver el catálogo como invitado', 'url': f"{reverse('catalogo')}?guest=1"},
                    {'label': 'Explorar la página por mi cuenta', 'url': '#', 'kind': 'dismiss_proactive'},
                ],
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
            {'label': 'Mis cotizaciones', 'url': reverse('cliente_cotizaciones_recibidas'), 'tour_id': 'quote-ready'},
            {'label': 'Mis pedidos', 'url': reverse('cliente_historial_ordenes'), 'tour_id': 'reorder'},
        ],
    })
    state, _ = AssistantUserState.objects.get_or_create(
        visitor_id=get_visitor_id(request),
        defaults={'user': user, 'cliente': cliente},
    )
    if not state.onboarding_completed:
        context['proactive'] = {
            'kind': 'first_authenticated_login',
            'message': '¡Bienvenido! Ya puedes usar tu cuenta. ¿Quieres que te muestre la plataforma o te ayudo con tu primer pedido?',
            'actions': [
                {'label': 'Hacer mi primer pedido', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
                {'label': 'Explorar por mi cuenta', 'url': '#', 'kind': 'dismiss_proactive'},
            ],
        }
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
            'id': latest_event.id,
            'type': latest_event.event_type,
            'entity_type': latest_event.entity_type,
            'entity_id': latest_event.entity_id,
            'payload': latest_event.payload,
        }
    return context
