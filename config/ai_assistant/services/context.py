from django.urls import reverse
from django.utils import timezone
from datetime import datetime, timedelta

from config.ai_assistant.models import AssistantConfiguration, AssistantDomainEvent, AssistantUserState
from config.ai_assistant.services.identity import get_customer_for_user, get_visitor_id, get_visitor_profile
from config.ai_assistant.services.customer_success_profile import touch_success_profile
from config.ai_assistant.services.customer_success import build_customer_success_summary
from config.ai_assistant.services.customer_event_engine import resolve_customer_event
from config.ai_assistant.services.language import resolve_request_language
from config.cotizaciones.models import Cotizacion
from config.pedidos.client_history import list_cliente_favorite_product_ids, list_cliente_purchase_orders


MODULE_KEYWORDS = (
    ('cart', ('cart', 'mi-orden', 'ver-cotizacion', 'my-order')),
    ('quotes', ('quote', 'cotizacion', 'cotizaciones')),
    ('orders', ('order-history', 'orders', 'pedido', 'ordenes', 'historial')),
    ('invoices', ('invoice', 'factura', 'billing')),
    ('catalog', ('catalog', 'catalogo', 'product')),
)


def current_module(page):
    """Map the page the customer is on to the module the answer belongs to."""
    normalized = str(page or '').lower()
    for module, keywords in MODULE_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return module
    return 'home'


def customer_display_name(user, cliente):
    """The name the assistant uses to address the customer."""
    first_name = str(getattr(user, 'first_name', '') or '').strip()
    if first_name:
        return first_name.split()[0]
    return str(getattr(cliente, 'nombre_empresa', '') or '').strip() if cliente else ''


def build_customer_context(request):
    """Small, scoped context. Volatile commerce values are queried by tools."""
    user = request.user
    cliente = get_customer_for_user(user)
    visitor_profile, _ = get_visitor_profile(request)
    language = resolve_request_language(request)
    assistant_name = AssistantConfiguration.get_solo().assistant_name
    context = {
        'authenticated': bool(getattr(user, 'is_authenticated', False)),
        'role': getattr(user, 'role', '') if getattr(user, 'is_authenticated', False) else '',
        'page': str(getattr(request, 'assistant_page', '') or request.GET.get('ai_page') or request.path)[:80],
        'language': language,
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
            'label': 'Start guided registration' if language == 'en' else 'Iniciar registro guiado',
            'url': reverse('home'),
            'tour_id': 'registration',
        }
        quiet = visitor_profile.quiet_until and visitor_profile.quiet_until > timezone.now()
        if visitor_profile.first_visit_prompted_at is None and not quiet:
            if language == 'en':
                context['proactive'] = {
                    'kind': 'first_visit',
                    'auto_open': True,
                    'message': (
                        f'Hi! 👋\n\nWelcome to La Tortilla Grocery LLC.\n\n'
                        f'I\'m {assistant_name}, your virtual assistant. '
                        'I see this is your first visit. How can I help you today?'
                    ),
                    'actions': [
                        {'label': 'Register as a customer', 'url': reverse('home'), 'tour_id': 'registration'},
                        {'label': 'Browse the catalog as a guest', 'url': f"{reverse('catalogo')}?guest=1"},
                        {'label': 'Explore on my own', 'url': '#', 'kind': 'dismiss_proactive'},
                    ],
                }
            else:
                context['proactive'] = {
                    'kind': 'first_visit',
                    'auto_open': True,
                    'message': (
                        '¡Hola! 👋\n\nBienvenido a La Tortilla Grocery LLC.\n\n'
                        f'Soy {assistant_name}, tu asistente virtual. '
                        'Veo que es tu primera visita. ¿En qué puedo ayudarte hoy?'
                    ),
                    'actions': [
                        {'label': 'Registrarme como cliente', 'url': reverse('home'), 'tour_id': 'registration'},
                        {'label': 'Ver el catálogo como invitado', 'url': f"{reverse('catalogo')}?guest=1"},
                        {'label': 'Explorar la página por mi cuenta', 'url': '#', 'kind': 'dismiss_proactive'},
                    ],
                }
        return context

    context['customer_name'] = customer_display_name(user, cliente)
    if language == 'en':
        catalog_actions = [
            {'label': 'View catalog', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
            {'label': 'My order', 'url': reverse('ver_cotizacion')},
            {'label': 'My quotes', 'url': reverse('cliente_cotizaciones_recibidas'), 'tour_id': 'quote-ready'},
            {'label': 'My orders', 'url': reverse('cliente_historial_ordenes'), 'tour_id': 'reorder'},
        ]
    else:
        catalog_actions = [
            {'label': 'Ver catálogo', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
            {'label': 'Mi orden', 'url': reverse('ver_cotizacion')},
            {'label': 'Mis cotizaciones', 'url': reverse('cliente_cotizaciones_recibidas'), 'tour_id': 'quote-ready'},
            {'label': 'Mis pedidos', 'url': reverse('cliente_historial_ordenes'), 'tour_id': 'reorder'},
        ]
    context.update({
        'customer': {
            'first_name': (user.first_name or '').strip(),
            'company': cliente.nombre_empresa,
            'approved': bool(cliente.aprobado),
            'review_status': cliente.estado_revision,
            'payment_terms': cliente.get_terminos_pago_label(),
        },
        'actions': catalog_actions,
    })
    state, _ = AssistantUserState.objects.get_or_create(
        visitor_id=get_visitor_id(request),
        defaults={'user': user, 'cliente': cliente},
    )
    success_profile = touch_success_profile(cliente=cliente, module=context['page'])
    context['customer_success'] = {
        'first_login_at': success_profile.first_login_at.isoformat() if success_profile and success_profile.first_login_at else None,
        'last_module': success_profile.last_module if success_profile else '',
        'last_tour': success_profile.last_tour if success_profile else '',
        'recent_products': success_profile.recently_viewed_products if success_profile else [],
    }
    context['assistant_memory'] = {
        key: value for key, value in (state.preferences or {}).items()
        if key in {'last_product_id', 'last_product_name', 'last_module', 'last_tour', 'language'}
    }
    quiet = visitor_profile.quiet_until and visitor_profile.quiet_until > timezone.now()
    if not state.onboarding_completed and not quiet:
        name_part = f', {context["customer_name"]}' if context['customer_name'] else ''
        if language == 'en':
            context['proactive'] = {
                'kind': 'first_authenticated_login',
                'auto_open': True,
                'message': (
                    f'Welcome{name_part}! '
                    'Your account is ready. Want a quick tour of the platform, or help with your first order?'
                ),
                'actions': [
                    {'label': 'Learn the platform', 'url': f"{reverse('catalogo')}?ai_tour=platform-catalog", 'tour_id': 'platform-catalog'},
                    {'label': 'Place my first order', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
                    {'label': 'Explore on my own', 'url': '#', 'kind': 'dismiss_proactive'},
                ],
            }
        else:
            context['proactive'] = {
                'kind': 'first_authenticated_login',
                'auto_open': True,
                'message': (
                    f'¡Bienvenido{name_part}! '
                    'Ya puedes usar tu cuenta. ¿Quieres que te muestre la plataforma o te ayudo con tu primer pedido?'
                ),
                'actions': [
                    {'label': 'Conocer la plataforma', 'url': f"{reverse('catalogo')}?ai_tour=platform-catalog", 'tour_id': 'platform-catalog'},
                    {'label': 'Hacer mi primer pedido', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
                    {'label': 'Explorar por mi cuenta', 'url': '#', 'kind': 'dismiss_proactive'},
                ],
            }
    elif success_profile:
        # Greeting stays available when the customer opens Isabella, but must not
        # steal the screen on every catalog search / page reload (critical on iOS).
        customer_name = (user.first_name or cliente.nombre_empresa or ('customer' if language == 'en' else 'cliente')).strip()
        if language == 'en':
            context['proactive'] = {
                'kind': 'returning_customer',
                'auto_open': False,
                'message': (
                    f'Hi {customer_name} 👋\n\n'
                    'Welcome back to La Tortilla Grocery LLC. How can I help you today?'
                ),
                'actions': [
                    {'label': 'View catalog', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
                    {'label': 'View my order', 'url': reverse('ver_cotizacion')},
                    {'label': 'Talk with sales manager', 'url': '#', 'kind': 'contact_handoff'},
                ],
            }
        else:
            context['proactive'] = {
                'kind': 'returning_customer',
                'auto_open': False,
                'message': (
                    f'Hola {customer_name} 👋\n\n'
                    'Bienvenido nuevamente a La Tortilla Grocery LLC. ¿En qué puedo ayudarte hoy?'
                ),
                'actions': [
                    {'label': 'Ver catálogo', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
                    {'label': 'Ver mi pedido', 'url': reverse('ver_cotizacion')},
                    {'label': 'Hablar con el gerente de ventas', 'url': '#', 'kind': 'contact_handoff'},
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
    success_summary = build_customer_success_summary(cliente=cliente, cart=cart)
    context['customer_success_summary'] = success_summary
    if success_profile and success_summary.get('last_order'):
        last_order = success_summary['last_order']
        success_profile.last_order_id = last_order['id']
        if success_profile.first_order_at is None:
            success_profile.first_order_at = datetime.fromisoformat(last_order['created_at'])
            success_profile.save(update_fields=['last_order_id', 'first_order_at', 'updated_at'])
        else:
            success_profile.save(update_fields=['last_order_id', 'updated_at'])
    if state.onboarding_completed:
        customer_event = resolve_customer_event(
            cliente=cliente,
            profile=success_profile,
            summary=success_summary,
            language=language,
        )
        if customer_event and not quiet:
            customer_name = (
                user.first_name or cliente.nombre_empresa or ('customer' if language == 'en' else 'cliente')
            ).strip()
            context['customer_event'] = customer_event
            greeting = f'Hi {customer_name} 👋\n\n' if language == 'en' else f'Hola {customer_name} 👋\n\n'
            extra_actions = [
                {
                    'label': 'New order' if language == 'en' else 'Nuevo pedido',
                    'url': reverse('catalogo'),
                    'tour_id': 'first-order',
                },
                {
                    'label': 'Talk with sales manager' if language == 'en' else 'Hablar con el gerente de ventas',
                    'url': '#',
                    'kind': 'contact_handoff',
                },
                {
                    'label': 'Keep shopping' if language == 'en' else 'Seguir comprando',
                    'url': '#',
                    'kind': 'dismiss_proactive',
                },
            ]
            context['proactive'] = {
                'kind': 'customer_success',
                'auto_open': True,
                'message': f'{greeting}{customer_event["message"]}',
                'actions': customer_event['actions'] + extra_actions,
            }
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
