"""Normalize and resolve the language Isabella should use with a customer."""

import re
import unicodedata

# Distinctive cues after accent folding. Shared function words are omitted so a
# single "de"/"the" never flips the conversation language.
_SPANISH_CUES = frozenset({
    'como', 'puedo', 'quiero', 'pero', 'tus', 'precios', 'precio', 'informacion',
    'formacion', 'este', 'esta', 'estos', 'estas', 'medio', 'estan',
    'donde', 'gracias', 'hola', 'necesito', 'tienen', 'tienes', 'cuanto',
    'tambien', 'cotizacion', 'cotizaciones', 'factura', 'facturas', 'pedido',
    'pedidos', 'sesion', 'iniciar', 'cuenta', 'ayuda', 'ayudame', 'buscar',
    'busco', 'comprar', 'agregar', 'producto', 'productos', 'oferta', 'ofertas',
    'promocion', 'promociones', 'saludo', 'buenas', 'buenos', 'dias', 'tardes',
    'favor', 'porfa', 'claro', 'vale', 'entonces', 'ahora', 'despues', 'antes',
    'ningun', 'ninguna', 'algun', 'alguna', 'nuestro', 'nuestra', 'ustedes',
})
_ENGLISH_CUES = frozenset({
    'how', 'what', 'can', 'want', 'but', 'your', 'prices', 'price', 'information',
    'through', 'where', 'thanks', 'thank', 'hello', 'hi', 'need', 'have', 'much',
    'also', 'quote', 'quotes', 'invoice', 'invoices', 'order', 'orders', 'login',
    'sign', 'account', 'help', 'please', 'search', 'looking', 'buy', 'add',
    'product', 'products', 'special', 'specials', 'promotion', 'promotions',
    'good', 'morning', 'afternoon', 'evening', 'then', 'now', 'after', 'before',
    'any', 'some', 'our', 'you', 'with', 'from', 'about',
})


def normalize_assistant_language(value, default='es'):
    raw = str(value or default).strip().lower().replace('_', '-')
    if raw.startswith('en'):
        return 'en'
    return 'es'


def _fold_message(message):
    text = unicodedata.normalize('NFKD', str(message or '').lower())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9\s]+', ' ', text)


def detect_message_language(message, default=None):
    """Infer es/en from the customer's wording when the signal is clear.

    Returns ``default`` (or None) when the message is too short or mixed to judge.
    """
    folded = _fold_message(message)
    tokens = [token for token in folded.split() if token]
    if not tokens:
        return normalize_assistant_language(default) if default else None

    spanish = sum(1 for token in tokens if token in _SPANISH_CUES)
    english = sum(1 for token in tokens if token in _ENGLISH_CUES)
    # Accented Spanish punctuation is a strong prior even after folding removed marks.
    original = str(message or '')
    if re.search(r'[¿¡áéíóúñÁÉÍÓÚÑ]', original):
        spanish += 2

    if spanish >= english + 1 and spanish > 0:
        return 'es'
    if english >= spanish + 1 and english > 0:
        return 'en'
    if default is not None:
        return normalize_assistant_language(default)
    return None


def sync_conversation_language_from_message(conversation, message):
    """Keep the thread language aligned with what the customer just wrote."""
    detected = detect_message_language(message)
    if not detected:
        return normalize_assistant_language(getattr(conversation, 'language', None) or 'es')
    current = normalize_assistant_language(getattr(conversation, 'language', None) or 'es')
    if current != detected:
        conversation.language = detected
        conversation.save(update_fields=['language'])
    return detected


def resolve_request_language(request, default='es'):
    """Prefer an explicit AI language (query or request attr) over the site locale."""
    explicit = ''
    if getattr(request, 'method', 'GET') == 'GET':
        explicit = request.GET.get('language') or ''
    if not explicit:
        explicit = getattr(request, 'assistant_language', None) or ''
    if explicit:
        return normalize_assistant_language(explicit, default=default)
    site_lang = str(getattr(request, 'LANGUAGE_CODE', '') or '')
    return normalize_assistant_language(site_lang or default, default=default)


def welcome_message_for(assistant_name, language='es'):
    from django.conf import settings

    if getattr(settings, 'DEMO_MODE', False):
        from config.ai_assistant.services.demo_assistant import demo_welcome_message

        return demo_welcome_message(language=normalize_assistant_language(language))

    name = str(assistant_name or 'Isabella').strip() or 'Isabella'
    if language == 'en':
        return (
            f'Hi. I\'m {name}, the virtual assistant for La Tortilla Grocery LLC.\n\n'
            'How can I help you?'
        )
    return (
        f'Hola. Soy {name}, el asistente virtual de La Tortilla Grocery LLC.\n\n'
        '¿En qué te puedo ayudar?'
    )
