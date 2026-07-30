import json

from django.urls import reverse

from config.ai_assistant.services.identity import get_customer_for_user
from config.ai_assistant.services.actions import (
    ACTION_ACCEPT_QUOTE,
    ACTION_ADD_CART,
    ACTION_CANCEL_QUOTE,
    ACTION_REMOVE_CART,
    ACTION_REORDER,
    ACTION_SUBMIT_QUOTE_REQUEST,
    ACTION_UPDATE_CART,
    create_pending_action,
)
from config.productos.models import Presentacion
from config.productos.promotions import promociones_activas_queryset
from config.cotizaciones.models import Cotizacion
from config.pedidos.client_history import list_cliente_purchase_orders


def openai_tool_schemas():
    return [
        {
            'type': 'function',
            'name': 'get_account_status',
            'description': 'Get the authenticated customer approval status and next recommended action.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
        {
            'type': 'function',
            'name': 'request_account_status_code',
            'description': 'Send a one-time verification code to the registered email before disclosing an unauthenticated account status.',
            'parameters': {
                'type': 'object',
                'properties': {'email': {'type': 'string'}},
                'required': ['email'],
                'additionalProperties': False,
            },
        },
        {
            'type': 'function',
            'name': 'verify_account_status_code',
            'description': 'Verify the one-time code and return only the verified account application status.',
            'parameters': {
                'type': 'object',
                'properties': {'challenge_id': {'type': 'string'}, 'code': {'type': 'string'}},
                'required': ['challenge_id', 'code'],
                'additionalProperties': False,
            },
        },
        {
            'type': 'function',
            'name': 'search_catalog',
            'description': 'Search active La Tortilla Grocery products by name. Never invent prices or promotions.',
            'parameters': {
                'type': 'object',
                'properties': {'query': {'type': 'string', 'description': 'Product name or category search.'}},
                'required': ['query'],
                'additionalProperties': False,
            },
        },
        {
            'type': 'function',
            'name': 'get_customer_next_steps',
            'description': 'Get safe links to catalog, cart, quotes and order history for the authenticated customer.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
        {
            'type': 'function',
            'name': 'propose_add_to_order',
            'description': 'Prepare an item to add to the customer order. This does not change the cart; the customer must explicitly confirm.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'presentation_id': {'type': 'integer'},
                    'quantity': {'type': 'integer', 'minimum': 1},
                },
                'required': ['presentation_id', 'quantity'],
                'additionalProperties': False,
            },
        },
        {
            'type': 'function',
            'name': 'get_cart_summary',
            'description': 'Get a current read-only summary of the customer session order.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
        {
            'type': 'function',
            'name': 'get_quotes_and_orders',
            'description': 'Get the authenticated customer own ready quotes and recent order statuses only.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
        {
            'type': 'function',
            'name': 'propose_cart_change',
            'description': 'Prepare a quantity update or removal from the customer order. Requires explicit confirmation and never changes the cart immediately.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'cart_key': {'type': 'string'},
                    'operation': {'type': 'string', 'enum': ['update', 'remove']},
                    'quantity': {'type': 'integer', 'minimum': 1},
                },
                'required': ['cart_key', 'operation'],
                'additionalProperties': False,
            },
        },
        {
            'type': 'function',
            'name': 'propose_reorder',
            'description': 'Prepare loading one of the authenticated customer previous orders into My Order. Requires confirmation.',
            'parameters': {
                'type': 'object',
                'properties': {'pedido_id': {'type': 'integer'}},
                'required': ['pedido_id'],
                'additionalProperties': False,
            },
        },
        {
            'type': 'function',
            'name': 'propose_quote_decision',
            'description': 'Prepare sending a quote request, accepting a ready quote with terms, or cancelling a ready quote. Requires explicit confirmation.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'operation': {'type': 'string', 'enum': ['submit_request', 'accept', 'cancel']},
                    'quote_token': {'type': 'string'},
                    'note': {'type': 'string'},
                },
                'required': ['operation'],
                'additionalProperties': False,
            },
        },
    ]


def _account_status(request):
    cliente = get_customer_for_user(request.user)
    if cliente is None:
        return {
            'authenticated': False,
            'status': 'visitor',
            'next_step': {'label': 'Registrarme', 'url': reverse('registro_usuario'), 'tour_id': 'registration'},
        }
    from config.ai_assistant.services.status_gateway import StatusGateway

    status = StatusGateway().get_status(cliente=cliente, entity_type='account')
    return {
        'authenticated': True,
        'approved': bool(cliente.aprobado),
        'review_status': cliente.estado_revision,
        'company': cliente.nombre_empresa,
        'status_details': status,
        'next_step': (
            {'label': 'Ver catálogo', 'url': reverse('catalogo'), 'tour_id': 'first-order'}
            if cliente.aprobado else
            {'label': 'Iniciar sesión', 'url': reverse('login'), 'tour_id': 'approved-login'}
        ),
    }


def _request_account_status_code(email):
    from config.ai_assistant.services.verification import VerificationRateLimited, issue_account_status_challenge

    try:
        challenge = issue_account_status_challenge(email)
    except VerificationRateLimited:
        return {'error': 'Too many code requests. Please try again later.'}
    return {
        'message': 'If the email is registered, a verification code was sent. Enter it here to continue.',
        'challenge_id': str(challenge.public_id),
    }


def _verify_account_status_code(challenge_id, code):
    from config.ai_assistant.services.status_gateway import StatusGateway
    from config.ai_assistant.services.verification import verify_account_status_challenge

    cliente = verify_account_status_challenge(challenge_id, code)
    if cliente is None:
        return {'error': 'The verification code is invalid, expired, or has already been used.'}
    return StatusGateway().get_status(cliente=cliente, entity_type='account')


def _search_catalog(request, query):
    queryset = (
        Presentacion.objects.filter(producto__activo=True, producto__nombre__icontains=str(query or '').strip())
        .select_related('producto')
        .order_by('producto__nombre', 'nombre')[:8]
    )
    cliente = get_customer_for_user(request.user)
    promotions = promociones_activas_queryset(cliente=cliente) if cliente else promociones_activas_queryset()
    promotion_product_ids = set(promotions.values_list('productos__id', flat=True))
    return {
        'products': [
            {
                'presentation_id': presentation.id,
                'product': presentation.producto.nombre,
                'presentation': presentation.nombre_empaque_cliente,
                'has_active_promotion': presentation.producto_id in promotion_product_ids,
                'catalog_url': reverse('catalogo'),
            }
            for presentation in queryset
        ]
    }


def _next_steps(request):
    if get_customer_for_user(request.user) is None:
        return _account_status(request)
    return {
        'actions': [
            {'label': 'Ver catálogo', 'url': reverse('catalogo'), 'tour_id': 'first-order'},
            {'label': 'Ver mi orden', 'url': reverse('ver_cotizacion')},
            {'label': 'Ver cotizaciones', 'url': reverse('cliente_cotizaciones_recibidas'), 'tour_id': 'quote-ready'},
            {'label': 'Ver pedidos', 'url': reverse('cliente_historial_ordenes'), 'tour_id': 'reorder'},
        ]
    }


def _propose_add_to_order(request, presentation_id, quantity):
    cliente = get_customer_for_user(request.user)
    if cliente is None or not cliente.aprobado:
        return {'error': 'Customer approval and login are required before adding products to an order.'}
    presentation = Presentacion.objects.select_related('producto').filter(
        pk=presentation_id,
        producto__activo=True,
    ).first()
    if presentation is None:
        return {'error': 'Product presentation not found.'}
    action = create_pending_action(
        request=request,
        action_type=ACTION_ADD_CART,
        payload={'presentation_id': presentation.id, 'quantity': max(int(quantity or 1), 1)},
    )
    return {
        'requires_confirmation': True,
        'action_id': str(action.public_id),
        'product': presentation.producto.nombre,
        'presentation': presentation.nombre_empaque_cliente,
        'quantity': max(int(quantity or 1), 1),
    }


def _propose_customer_action(request, action_type, payload, label):
    cliente = get_customer_for_user(request.user)
    if cliente is None or not cliente.aprobado:
        return {'error': 'Customer approval and login are required for this action.'}
    action = create_pending_action(request=request, action_type=action_type, payload=payload)
    return {
        'requires_confirmation': True,
        'action_id': str(action.public_id),
        'label': label,
    }


def _propose_cart_change(request, operation, cart_key, quantity=None):
    if operation == 'update':
        return _propose_customer_action(
            request,
            ACTION_UPDATE_CART,
            {'cart_key': str(cart_key), 'quantity': quantity},
            'Update this order quantity',
        )
    if operation == 'remove':
        return _propose_customer_action(
            request,
            ACTION_REMOVE_CART,
            {'cart_key': str(cart_key)},
            'Remove this product from My Order',
        )
    return {'error': 'Unsupported cart operation.'}


def _propose_reorder(request, pedido_id):
    return _propose_customer_action(
        request, ACTION_REORDER, {'pedido_id': pedido_id}, 'Load this previous order into My Order'
    )


def _propose_quote_decision(request, operation, quote_token='', note=''):
    action_types = {
        'submit_request': (ACTION_SUBMIT_QUOTE_REQUEST, 'Send My Order as a quote request'),
        'accept': (ACTION_ACCEPT_QUOTE, 'Accept this quote and its terms'),
        'cancel': (ACTION_CANCEL_QUOTE, 'Cancel this quote'),
    }
    if operation not in action_types:
        return {'error': 'Unsupported quote operation.'}
    action_type, label = action_types[operation]
    return _propose_customer_action(
        request, action_type, {'quote_token': quote_token, 'note': str(note or '')[:1000]}, label
    )


def _cart_summary(request):
    lines = []
    for item in (request.session.get('carrito', {}) or {}).values():
        if not isinstance(item, dict):
            continue
        lines.append({
            'presentation_id': item.get('presentacion_id'),
            'name': str(item.get('nombre') or '')[:120],
            'quantity': int(item.get('cantidad') or 0),
        })
    return {'items': lines, 'total_units': sum(item['quantity'] for item in lines), 'cart_url': reverse('ver_cotizacion')}


def _quotes_and_orders(request):
    cliente = get_customer_for_user(request.user)
    if cliente is None:
        return {'error': 'Customer login required.'}
    return {
        'ready_quotes': [
            {
                'id': quote.id,
                'total': str(quote.total),
                'url': reverse('cliente_cotizacion_recibida_detalle', args=[quote.token_cliente]),
            }
            for quote in Cotizacion.objects.filter(cliente=cliente, estado='LISTA_PARA_CONFIRMACION').order_by('-fecha')[:5]
        ],
        'recent_orders': [
            {'id': order.id, 'status': order.estado}
            for order in list_cliente_purchase_orders(cliente=cliente)[:5]
        ],
    }


def execute_tool(request, name, raw_arguments):
    try:
        arguments = json.loads(raw_arguments or '{}')
    except (TypeError, json.JSONDecodeError):
        return {'error': 'Invalid tool arguments.'}
    if name == 'get_account_status':
        return _account_status(request)
    if name == 'request_account_status_code':
        return _request_account_status_code(arguments.get('email', ''))
    if name == 'verify_account_status_code':
        return _verify_account_status_code(arguments.get('challenge_id', ''), arguments.get('code', ''))
    if name == 'search_catalog':
        return _search_catalog(request, arguments.get('query', ''))
    if name == 'get_customer_next_steps':
        return _next_steps(request)
    if name == 'propose_add_to_order':
        return _propose_add_to_order(request, arguments.get('presentation_id'), arguments.get('quantity'))
    if name == 'get_cart_summary':
        return _cart_summary(request)
    if name == 'get_quotes_and_orders':
        return _quotes_and_orders(request)
    if name == 'propose_cart_change':
        return _propose_cart_change(request, arguments.get('operation'), arguments.get('cart_key'), arguments.get('quantity'))
    if name == 'propose_reorder':
        return _propose_reorder(request, arguments.get('pedido_id'))
    if name == 'propose_quote_decision':
        return _propose_quote_decision(
            request,
            arguments.get('operation'),
            arguments.get('quote_token', ''),
            arguments.get('note', ''),
        )
    return {'error': 'Tool is not allowed.'}
