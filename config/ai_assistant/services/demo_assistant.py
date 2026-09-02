"""Zyntra Software Lab demo assistant — mock replies, no OpenAI / no LTG branding."""

from __future__ import annotations

from django.conf import settings

from config.core.demo_branding import (
    DEMO_EMAIL,
    DEMO_PHONE_DISPLAY,
    DEMO_WHATSAPP_E164,
    get_demo_brand_name,
)

DEMO_ASSISTANT_NAME = 'Zyntra Guide'
DEMO_ASSISTANT_TAGLINE = 'Software Lab demo assistant · fictitious help'


def is_demo_assistant_mode():
    if bool(getattr(settings, 'DEMO_MODE', False)):
        return True
    provider = str(getattr(settings, 'AI_ASSISTANT_PROVIDER', 'live') or 'live').strip().lower()
    return provider == 'mock'


def apply_demo_assistant_config(config, *, save=False):
    """Overlay AssistantConfiguration with Zyntra demo identity + fake contacts."""
    if config is None or not is_demo_assistant_mode():
        return config
    brand = get_demo_brand_name()
    config.assistant_name = DEMO_ASSISTANT_NAME
    config.welcome_message = (
        f'Hi. I\'m {DEMO_ASSISTANT_NAME}, the demo assistant for {brand}.\n\n'
        'I can guide you through the Software Lab with fictitious data — '
        'catalog, orders, QuickBooks mock, and the guided tour.\n\n'
        'How can I help?'
    )
    config.personality = 'Clear, concise, demo-focused. Never invent production facts.'
    config.sales_goal = 'Help visitors explore the Zyntra Software Lab demo safely.'
    config.support_phone = DEMO_PHONE_DISPLAY
    config.support_whatsapp = DEMO_WHATSAPP_E164
    config.support_email = DEMO_EMAIL
    config.location_address = '500 Harbor Lab Avenue, Suite 12\nAustin, TX 78701'
    config.delivery_coverage = 'Demo coverage · Software Lab only'
    config.enabled = bool(getattr(settings, 'AI_ASSISTANT_ENABLED', True))
    config.enable_home = True
    config.enable_catalog = True
    config.enable_customer_portal = True
    if save:
        config.save()
    return config


def demo_welcome_message(language='en'):
    brand = get_demo_brand_name()
    if language == 'es':
        return (
            f'Hola. Soy {DEMO_ASSISTANT_NAME}, el asistente demo de {brand}.\n\n'
            'Puedo guiarte por el Software Lab con datos ficticios: '
            'catálogo, pedidos, QuickBooks mock y el tour guiado.\n\n'
            '¿En qué te ayudo?'
        )
    return (
        f'Hi. I\'m {DEMO_ASSISTANT_NAME}, the demo assistant for {brand}.\n\n'
        'I can guide you through the Software Lab with fictitious data — '
        'catalog, orders, QuickBooks mock, and the guided tour.\n\n'
        'How can I help?'
    )


def demo_fallback_response(config, context, message):
    """Scripted replies for DEMO — never call OpenAI."""
    brand = get_demo_brand_name()
    name = getattr(config, 'assistant_name', None) or DEMO_ASSISTANT_NAME
    lower = (message or '').lower()
    lang = context.get('language') or 'en'
    actions = list(context.get('actions') or [])[:2]
    next_action = context.get('next_recommended_action')
    if next_action and next_action not in actions:
        actions = [next_action, *actions][:2]

    def _msg(en, es):
        return es if lang == 'es' else en

    if any(term in lower for term in ('quickbooks', 'qb', 'contabilidad', 'sync', 'sincron')):
        return {
            'message': _msg(
                f'In this Software Lab, QuickBooks runs in mock/sandbox mode. '
                f'Open QuickBooks Center from the admin panel to connect, preview, and sync fictitious data — no Intuit production traffic.',
                f'En este Software Lab, QuickBooks corre en modo mock/sandbox. '
                f'Abre QuickBooks Center desde el panel admin para conectar, previsualizar y sincronizar datos ficticios — sin tráfico a producción Intuit.',
            ),
            'suggested_actions': actions,
            'tour_id': '',
        }

    if any(term in lower for term in ('tour', 'tour guiado', 'recorrido', 'guide', 'demo')):
        return {
            'message': _msg(
                f'Use the Guided tour in the sidebar (Software Lab) to walk through the {brand} panel step by step.',
                f'Usa el Guided tour en el sidebar (Software Lab) para recorrer el panel de {brand} paso a paso.',
            ),
            'suggested_actions': actions,
            'tour_id': '',
        }

    if any(term in lower for term in ('catalog', 'catálogo', 'catalogo', 'producto', 'product')):
        return {
            'message': _msg(
                'Browse the demo catalog as a guest, or sign in with the demo account to request quotes. All SKUs and prices are fictitious.',
                'Explora el catálogo demo como invitado, o inicia sesión con la cuenta demo para pedir cotizaciones. Todos los SKUs y precios son ficticios.',
            ),
            'suggested_actions': actions,
            'tour_id': 'first-order' if context.get('authenticated') else 'registration',
        }

    if any(term in lower for term in ('login', 'iniciar', 'sesión', 'sesion', 'password', 'contraseña')):
        tour_id = 'password-recovery' if 'password' in lower or 'contraseña' in lower else 'login'
        return {
            'message': _msg(
                'I can guide you through sign-in. Demo explorer: demo@demo-system.com / DemoShowcase2026!',
                'Puedo guiarte para iniciar sesión. Explorador demo: demo@demo-system.com / DemoShowcase2026!',
            ),
            'suggested_actions': actions,
            'tour_id': tour_id,
        }

    if any(term in lower for term in ('register', 'registro', 'cuenta', 'account', 'sign up')):
        return {
            'message': _msg(
                f'You can create a demo wholesale account from Register — data stays in the Software Lab SQLite demo database, not {brand} production.',
                f'Puedes crear una cuenta mayorista demo desde Registro — los datos quedan en la SQLite del Software Lab, no en producción de {brand}.',
            ),
            'suggested_actions': actions,
            'tour_id': 'registration',
        }

    if not context.get('authenticated'):
        return {
            'message': _msg(
                f"Hi, I'm {name}. I help you explore the {brand} Software Lab with fictitious data. "
                f'Try the catalog, sign in with the demo account, or open the guided tour.',
                f'Hola, soy {name}. Te ayudo a explorar el Software Lab de {brand} con datos ficticios. '
                f'Prueba el catálogo, inicia sesión con la cuenta demo o abre el guided tour.',
            ),
            'suggested_actions': actions,
            'tour_id': 'registration',
        }

    return {
        'message': _msg(
            f"I'm {name}. Ask me about the demo catalog, orders pipeline, QuickBooks mock, or the guided tour. "
            f'Remember: everything here is fictitious Software Lab data.',
            f'Soy {name}. Pregúntame por el catálogo demo, el pipeline de pedidos, QuickBooks mock o el guided tour. '
            f'Recuerda: todo aquí son datos ficticios del Software Lab.',
        ),
        'suggested_actions': actions,
        'tour_id': '',
    }
