"""Normalize and resolve the language Isabella should use with a customer."""


def normalize_assistant_language(value, default='es'):
    raw = str(value or default).strip().lower().replace('_', '-')
    if raw.startswith('en'):
        return 'en'
    return 'es'


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
