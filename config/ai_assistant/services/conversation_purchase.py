import re
from datetime import datetime, timedelta

from django.utils import timezone

from config.ai_assistant.services.catalog_resolver import normalize_catalog_term


ORDINALS = {
    'primero': 1, 'primera': 1, '1': 1, 'uno': 1,
    'segundo': 2, 'segunda': 2, '2': 2, 'dos': 2,
    'tercero': 3, 'tercera': 3, '3': 3, 'tres': 3,
    'cuarto': 4, 'cuarta': 4, '4': 4, 'cuatro': 4,
    'quinto': 5, 'quinta': 5, '5': 5, 'cinco': 5,
}


def save_catalog_results(conversation, products):
    results = []
    for product in products[:5]:
        results.append({
            'product_id': product['product_id'],
            'name': product['name'],
            'brand': product.get('brand', ''),
            'presentations': [
                {'id': item['id'], 'name': item['name']}
                for item in product.get('presentations', [])[:8]
            ],
            'score': product.get('score', 0),
        })
    conversation.shopping_context = {
        'version': 1,
        'expires_at': (timezone.now() + timedelta(minutes=20)).isoformat(),
        'catalog_results': results,
    }
    conversation.save(update_fields=['shopping_context', 'last_activity_at'])
    return results


def get_catalog_results(conversation):
    context = conversation.shopping_context or {}
    raw_expiry = context.get('expires_at')
    try:
        expires_at = datetime.fromisoformat(raw_expiry)
        if timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at)
        if expires_at <= timezone.now():
            return []
    except (TypeError, ValueError):
        return []
    return context.get('catalog_results') or []


def resolve_catalog_reference(conversation, message):
    normalized = normalize_catalog_term(message)
    index = None
    for token, value in ORDINALS.items():
        if re.search(rf'\b{re.escape(token)}\b', normalized):
            index = value - 1
            break
    results = get_catalog_results(conversation)
    if index is None or index >= len(results):
        return None
    product = results[index]
    presentations = product.get('presentations') or []
    selected = next(
        (
            presentation for presentation in presentations
            if normalize_catalog_term(presentation['name']) in normalized
        ),
        presentations[0] if len(presentations) == 1 else None,
    )
    quantity_match = re.search(r'\b(\d{1,3})\b', normalized)
    return {
        'product': product,
        'presentation': selected,
        'quantity': int(quantity_match.group(1)) if quantity_match else None,
        'requires_presentation': bool(presentations) and selected is None,
    }
