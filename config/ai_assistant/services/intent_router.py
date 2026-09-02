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
PRICE_TERMS = ('precio', 'precios', 'price', 'prices', 'cost', 'costs')
CHECKOUT_TERMS = ('no', 'no gracias', 'nada mas', 'termine', 'finalizar', 'listo', 'eso es todo')
QUANTITY_UNITS = ('caja', 'cajas', 'unidad', 'unidades', 'case', 'cases', 'box', 'boxes')

# Words that belong to questions / platform talk, never to a catalog product name.
CONVERSATIONAL_STOPWORDS = frozenset({
    'como', 'puedo', 'saber', 'tus', 'tu', 'mis', 'mi', 'este', 'esta', 'estos', 'estas',
    'medio', 'obtener', 'informacion', 'formacion', 'pro', 'por', 'favor', 'please',
    'quiero', 'pero', 'ayuda', 'ayudame', 'how', 'can', 'know', 'your', 'get', 'see',
    'the', 'a', 'an', 'to', 'of', 'for', 'with', 'from', 'about', 'me', 'my', 'you',
    'ver', 'verlos', 'consultar', 'averiguar', 'necesito', 'busco', 'buscar', 'tienen',
    'tienes', 'hay', 'precio', 'precios', 'price', 'prices', 'cost', 'costs', 'producto',
    'productos', 'product', 'products', 'comprar', 'agregar', 'agrega', 'add', 'una',
    'un', 'unos', 'unas', 'las', 'los', 'del', 'de', 'la', 'el', 'en', 'al', 'si',
    'yes', 'no', 'que', 'what', 'where', 'donde', 'cuando', 'when', 'porque', 'why',
    'tambien', 'also', 'mas', 'more', 'solo', 'only', 'aqui', 'here', 'alli', 'there',
    'chat', 'asistente', 'assistant', 'plataforma', 'platform', 'cuenta', 'account',
    'sesion', 'login', 'aprobada', 'approved', 'lista', 'list', 'through', 'channel',
    'medium', 'this', 'that', 'esos', 'esas', 'eso', 'esa', 'hola', 'hello', 'hi',
    'gracias', 'thanks', 'thank', 'claro', 'ok', 'okay', 'vale', 'pues', 'entonces',
    'ahora', 'now', 'despues', 'antes', 'before', 'after', 'aun', 'todavia', 'still',
    'already', 'ya', 'mucho', 'much', 'poco', 'few', 'some', 'any', 'algun', 'alguna',
})

PRICE_ACCESS_PATTERNS = (
    r'como\s+(?:puedo\s+)?(?:saber|ver|obtener|consultar|averiguar).{0,48}(?:precio|precios|costo|costos)',
    r'(?:quiero|necesito)\s+(?:saber|ver|obtener|consultar).{0,48}(?:precio|precios)',
    r'(?:saber|ver|obtener|consultar)\s+(?:los\s+)?precios',
    r'lista\s+de\s+precios',
    r'how\s+(?:can\s+i\s+|do\s+i\s+)?(?:know|see|get|check|find).{0,48}(?:price|prices|cost)',
    r'(?:want|need)\s+to\s+(?:know|see|get|check).{0,48}(?:price|prices)',
    r'(?:see|get|check)\s+(?:the\s+)?prices',
    r'price\s+list',
    r'where\s+(?:can\s+i\s+)?(?:see|find|get).{0,24}(?:price|prices)',
)

PLATFORM_HOWTO_PATTERNS = (
    r'como\s+(?:puedo|hago|funciona)',
    r'how\s+(?:can|do|does)',
    r'por\s+este\s+medio',
    r'through\s+this',
    r'en\s+este\s+(?:chat|medio)',
    r'obtener\s+.{0,24}(?:informacion|formacion|info)',
    r'get\s+.{0,24}(?:information|info)\s+.{0,16}(?:chat|here|this)',
)


def _has(normalized, terms):
    return any(re.search(rf'(?:^|\s){re.escape(term)}(?:\s|$)', normalized) for term in terms)


def _contains(normalized, terms):
    return any(term in normalized for term in terms)


FILLER_TERMS = frozenset({
    'de', 'del', 'los', 'las', 'por', 'favor', 'please', 'quiero', 'necesito', 'dame',
    'ponme', 'agrega', 'agregar', 'add', 'mas', 'unos', 'unas', 'para',
})


def product_query_residual(message):
    """Return catalog-like tokens after stripping purchase and conversational filler.

    Empty residual means the message is not naming a product and must not trigger
    a literal catalog search (e.g. "cómo puedo saber precios").
    """
    normalized = normalize_catalog_term(message)
    residual = []
    for token in catalog_tokens(strip_quantity_noise(normalized)):
        if len(token) <= 2:
            continue
        if token in CONVERSATIONAL_STOPWORDS or token in FILLER_TERMS:
            continue
        residual.append(token)
    return residual


def is_price_access_question(message):
    """True when the customer asks how to see prices / get pricing access."""
    normalized = normalize_catalog_term(message)
    if any(re.search(pattern, normalized) for pattern in PRICE_ACCESS_PATTERNS):
        return not product_query_residual(message)
    if _has(normalized, PRICE_TERMS) and not product_query_residual(message):
        return True
    if any(re.search(pattern, normalized) for pattern in PLATFORM_HOWTO_PATTERNS):
        # "cómo puedo…" / "por este medio" without a product name → access help,
        # not a catalog lookup for fragments of the sentence.
        return not product_query_residual(message)
    return False


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

    # FAQ / "how do I see prices" must never become a literal catalog search.
    if is_price_access_question(message):
        return 'price_access'

    if _has(normalized, PURCHASE_TERMS) or _contains(normalized, ('producto', 'productos')):
        if not product_query_residual(message):
            if _has(normalized, PRICE_TERMS):
                return 'price_access'
            return 'conversation'
        return 'product_search'

    if context.get('authenticated') and _contains(normalized, ACCOUNT_TERMS):
        return 'customer_success'

    return 'conversation'
