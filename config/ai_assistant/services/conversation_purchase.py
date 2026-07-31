import re

from config.ai_assistant.services.catalog_resolver import normalize_catalog_term
from config.ai_assistant.services.conversation_state import (
    load_state,
    remember_catalog_results,
    select_product,
)

ORDINALS = {
    'primero': 1, 'primera': 1, 'uno': 1, 'una': 1,
    'segundo': 2, 'segunda': 2, 'dos': 2,
    'tercero': 3, 'tercera': 3, 'tres': 3,
    'cuarto': 4, 'cuarta': 4, 'cuatro': 4,
    'quinto': 5, 'quinta': 5, 'cinco': 5,
}

# "ese", "ese producto", "el mismo": the customer points at the current selection
# instead of naming a product again.
DEICTIC_TERMS = (
    'ese', 'esa', 'eso', 'este', 'esta', 'esto', 'aquel', 'aquella',
    'el mismo', 'la misma', 'lo mismo', 'that one', 'the same',
)


def save_catalog_results(conversation, products):
    return remember_catalog_results(conversation, products)


def get_catalog_results(conversation):
    return load_state(conversation).get('catalog_results') or []


def ordinal_index(normalized):
    for token, value in ORDINALS.items():
        if re.search(rf'\b{re.escape(token)}\b', normalized):
            return value - 1
    # "el 2", "opcion 2", "#2": an explicit pick from the enumerated list. A bare
    # number is deliberately excluded so "10 cajas" is read as a quantity.
    match = re.search(r'(?:\b(?:numero|number|opcion|option|el|la|los|las)\s*|#)(\d{1,2})\b', normalized)
    if match:
        return int(match.group(1)) - 1
    return None


def mentions_current_selection(normalized):
    return any(re.search(rf'\b{re.escape(term)}\b', normalized) for term in DEICTIC_TERMS)


def _resolve_presentation(product, normalized):
    presentations = product.get('presentations') or []
    for presentation in presentations:
        if normalize_catalog_term(presentation['name']) in normalized:
            return presentation
    return presentations[0] if len(presentations) == 1 else None


def resolve_catalog_reference(conversation, message):
    """Resolve "the first", "number 3" or "that product" against the stored state.

    Returns ``None`` only when the customer is not pointing at anything already
    shown, so the caller never has to guess a different product.
    """
    normalized = normalize_catalog_term(message)
    state = load_state(conversation)
    results = state.get('catalog_results') or []
    product = None

    index = ordinal_index(normalized)
    if index is not None and index < len(results):
        product = results[index]
    elif state.get('selected_product') and (
        mentions_current_selection(normalized) or re.search(r'\b\d{1,3}\b', normalized)
    ):
        product = state['selected_product']
    elif len(results) == 1 and mentions_current_selection(normalized):
        product = results[0]

    if product is None:
        return None

    presentation = _resolve_presentation(product, normalized)
    quantity_match = re.search(r'\b(\d{1,3})\b', normalized)
    select_product(conversation, product, presentation)
    return {
        'product': product,
        'presentation': presentation,
        'quantity': int(quantity_match.group(1)) if quantity_match else None,
        'requires_presentation': bool(product.get('presentations')) and presentation is None,
    }
