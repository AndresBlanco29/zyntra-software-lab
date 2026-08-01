"""Deterministic intent routing for the assistant agent.

The previous implementation evaluated handlers in a fixed cascade, so a message
such as "necesito 10 cajas de ese producto" could be captured by the customer
success handler (which also matches "pedido"/"factura") and answer about
invoices. Here one intent is resolved first, with product references winning
over account questions, and the orchestrator then runs a single handler.
"""

import re

from config.ai_assistant.services.catalog_resolver import (
    catalog_tokens,
    normalize_catalog_term,
    strip_quantity_noise,
)
from config.ai_assistant.services.conversation_purchase import (
    mentions_current_selection,
    ordinal_index,
)
from config.ai_assistant.services.conversation_state import load_state

CONTACT_TERMS = ('telefono', 'llamar', 'whatsapp', 'correo', 'email', 'contacto', 'direccion', 'ubicacion', 'donde estan')
PROMOTION_TERMS = ('oferta', 'ofertas', 'promocion', 'promociones', 'descuento', 'descuentos', 'special', 'specials')
BILLING_TERMS = (
    'factura', 'facturas', 'facturacion', 'invoice', 'invoices', 'billing',
    'saldo', 'debo', 'pago', 'pagos', 'payment', 'abono', 'credito', 'estado de cuenta',
    'vence', 'vencimiento', 'nota de credito', 'nota de debito',
)
ACCOUNT_TERMS = (
    'mi pedido', 'mis pedidos', 'mi orden', 'mis ordenes', 'mi cotizacion', 'mis cotizaciones',
    'estado de mi', 'ultima compra', 'favorito', 'favoritos',
    'my order', 'my orders', 'my quote', 'my quotes', 'my invoice', 'mi quote', 'mis quotes',
)
PURCHASE_TERMS = (
    'necesito', 'busco', 'buscar', 'quiero', 'comprar', 'tienen', 'tienes', 'hay',
    'precio', 'precios', 'price', 'cost', 'agregar', 'agrega', 'add',
)
CHECKOUT_TERMS = ('no', 'no gracias', 'nada mas', 'termine', 'finalizar', 'listo', 'eso es todo')
QUANTITY_UNITS = ('caja', 'cajas', 'unidad', 'unidades', 'case', 'cases', 'box', 'boxes')


def _has(normalized, terms):
    return any(re.search(rf'(?:^|\s){re.escape(term)}(?:\s|$)', normalized) for term in terms)


def _contains(normalized, terms):
    return any(term in normalized for term in terms)


FILLER_TERMS = frozenset({
    'de', 'del', 'los', 'las', 'por', 'favor', 'please', 'quiero', 'necesito', 'dame',
    'ponme', 'agrega', 'agregar', 'add', 'mas', 'unos', 'unas', 'para',
})


def _names_another_product(normalized, state):
    """True when the message still names a product after removing the amount.

    "10 cajas" is a quantity for the current product, but "10 cajas de jarritos
    mango" names a different one and must start a new search instead of silently
    setting a quantity on whatever was selected before.
    """
    residual = {
        token for token in catalog_tokens(strip_quantity_noise(normalized))
        if len(token) > 2 and token not in FILLER_TERMS
    }
    product = state.get('selected_product') or {}
    known = catalog_tokens(f"{product.get('name', '')} {product.get('brand', '')}")
    return bool(residual - known)


def _looks_like_quantity_reply(normalized):
    if not re.search(r'\b\d{1,3}\b', normalized):
        return False
    return _contains(normalized, QUANTITY_UNITS) or len(normalized.split()) <= 6


def _multi_line_count(message):
    lines = 0
    for raw_line in re.split(r'[\n,;]+', str(message or '')):
        if re.match(r'\s*\d{1,3}\s+\S+', raw_line):
            lines += 1
    return lines


def resolve_intent(*, conversation, message, context):
    """Return the single intent that must handle this turn.

    Order matters and is intentional: anything that points at the product the
    customer is already discussing outranks generic account questions.
    """
    normalized = normalize_catalog_term(message)
    state = load_state(conversation)
    has_selection = bool(state.get('selected_product'))
    has_results = bool(state.get('catalog_results'))

    if _contains(normalized, CONTACT_TERMS):
        return 'commercial_information'

    # Billing is not handled in this chat; it is handed off to a human agent.
    if _contains(normalized, BILLING_TERMS):
        return 'billing_handoff'

    if _contains(normalized, PROMOTION_TERMS):
        return 'promotions'

    # A reference to something already shown must never trigger a new search or
    # an account answer.
    references_selection = ordinal_index(normalized) is not None or mentions_current_selection(normalized)
    if (has_results or has_selection) and references_selection:
        return 'product_reference'

    if (
        has_selection
        and _looks_like_quantity_reply(normalized)
        and not _names_another_product(normalized, state)
    ):
        return 'product_reference'

    if _multi_line_count(message) >= 2:
        return 'multi_item_purchase'

    if normalized.strip() in CHECKOUT_TERMS:
        return 'checkout'

    if _contains(normalized, ACCOUNT_TERMS):
        # A visitor asking about their own orders, quotes or invoices must be
        # invited to sign in; their status can never be answered anonymously.
        if not context.get('authenticated'):
            return 'guest_account_status'
        if not _has(normalized, PURCHASE_TERMS):
            return 'customer_success'

    if _has(normalized, PURCHASE_TERMS) or _contains(normalized, ('producto', 'productos')):
        return 'product_search'

    if context.get('authenticated') and _contains(normalized, ACCOUNT_TERMS):
        return 'customer_success'

    return 'conversation'
