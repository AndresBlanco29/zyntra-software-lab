import re
from urllib.parse import quote

from config.ai_assistant.models import AssistantConfiguration


def _digits(value):
    return re.sub(r'\D+', '', value or '')


def build_contact_dto():
    from config.ai_assistant.services.demo_assistant import apply_demo_assistant_config, is_demo_assistant_mode
    from config.core.demo_branding import get_demo_brand_name

    config = AssistantConfiguration.get_solo()
    if is_demo_assistant_mode():
        apply_demo_assistant_config(config)
    phone_digits = _digits(config.support_phone)
    whatsapp_digits = _digits(config.support_whatsapp)
    email = config.support_email.strip()
    brand = get_demo_brand_name() if is_demo_assistant_mode() else 'La Tortilla Grocery'
    actions = []
    if phone_digits:
        actions.append({'label': 'Llamar', 'url': f'tel:+{phone_digits}', 'kind': 'contact'})
        actions.append({'label': 'SMS', 'url': f'sms:+{phone_digits}', 'kind': 'contact'})
    if whatsapp_digits:
        actions.append({
            'label': 'WhatsApp',
            'url': f'https://wa.me/{whatsapp_digits}',
            'kind': 'contact',
            'external': True,
        })
    if email:
        subject = (
            f'{brand} Software Lab demo'
            if is_demo_assistant_mode()
            else 'Consulta para La Tortilla Grocery'
        )
        actions.append({
            'label': 'Enviar correo',
            'url': f'mailto:{email}?subject={quote(subject)}',
            'kind': 'contact',
        })
    return {
        'phone': config.support_phone,
        'whatsapp': config.support_whatsapp,
        'email': email,
        'actions': actions,
    }


def build_location_dto():
    config = AssistantConfiguration.get_solo()
    return {
        'address': config.location_address.strip(),
        'map_url': config.location_map_url.strip(),
        'coverage': config.delivery_coverage.strip() or 'Georgia, Alabama y Tennessee',
    }
