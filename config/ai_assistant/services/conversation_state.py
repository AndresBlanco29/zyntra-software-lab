"""Single source of truth for the agent state of one assistant conversation.

Before this module the shopping context lived in two places: visitor-scoped
``AssistantUserState.preferences`` and ``AssistantConversation.shopping_context``.
Two writers meant the selected product could silently change between turns, so
every agent read/write now goes through here and stays scoped to one
conversation.
"""

from datetime import datetime, timedelta

from django.utils import timezone

STATE_VERSION = 2
STATE_TTL_MINUTES = 45

EMPTY_STATE = {
    'version': STATE_VERSION,
    'catalog_results': [],
    'selected_product': None,
    'selected_presentation': None,
    'pending_quantity': None,
    'last_intent': '',
    'last_tool': '',
    'module': '',
}


def _is_expired(raw_value):
    if not raw_value:
        return True
    try:
        expires_at = datetime.fromisoformat(raw_value)
    except (TypeError, ValueError):
        return True
    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at)
    return expires_at <= timezone.now()


def load_state(conversation):
    """Return the live agent state, or a clean state when missing or expired."""
    stored = conversation.shopping_context or {}
    if stored.get('version') != STATE_VERSION or _is_expired(stored.get('expires_at')):
        return dict(EMPTY_STATE)
    state = dict(EMPTY_STATE)
    state.update({key: value for key, value in stored.items() if key in EMPTY_STATE})
    return state


def update_state(conversation, **values):
    """Merge values into the conversation state and refresh its expiry."""
    state = load_state(conversation)
    state.update({key: value for key, value in values.items() if key in EMPTY_STATE})
    state['version'] = STATE_VERSION
    state['expires_at'] = (timezone.now() + timedelta(minutes=STATE_TTL_MINUTES)).isoformat()
    conversation.shopping_context = state
    conversation.save(update_fields=['shopping_context'])
    return state


def clear_state(conversation):
    conversation.shopping_context = {}
    conversation.save(update_fields=['shopping_context'])


def remember_catalog_results(conversation, products, *, limit=50):
    """Store the exact enumerated list shown to the customer."""
    results = [
        {
            'product_id': product['product_id'],
            'name': product['name'],
            'brand': product.get('brand', ''),
            'presentations': [
                {'id': item['id'], 'name': item['name']}
            for item in product.get('presentations', [])[:8]
            ],
        }
        for product in products[:limit]
    ]
    selected = results[0] if len(results) == 1 else None
    update_state(conversation, catalog_results=results, selected_product=selected)
    return results


def select_product(conversation, product, presentation=None):
    return update_state(conversation, selected_product=product, selected_presentation=presentation)


def selected_product(conversation):
    return load_state(conversation).get('selected_product')
