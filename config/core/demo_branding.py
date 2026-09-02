"""Provisional DEMO brand identity (Zyntra). Production LTG branding is untouched."""

from django.conf import settings

DEFAULT_BRAND_NAME = 'Zyntra'
DEFAULT_BRAND_LEGAL_NAME = 'Zyntra'
DEFAULT_BRAND_TAGLINE = 'B2B distribution operations platform'
DEFAULT_BRAND_ACCENT = '#A78BFA'
DEFAULT_BRAND_PRIMARY = '#0B1224'
DEFAULT_BRAND_INK = '#041018'

# Fictitious Software Lab contacts — never reuse LTG numbers/addresses.
DEMO_PHONE_DISPLAY = '+1 (555) 014-2088'
DEMO_PHONE_E164 = '+15550142088'
DEMO_WHATSAPP_DISPLAY = '+1 (555) 019-4472'
DEMO_WHATSAPP_E164 = '15550194472'
DEMO_EMAIL = 'hello@zyntra-demo.example'
DEMO_ADDRESS_LINE_1 = '500 Harbor Lab Avenue, Suite 12'
DEMO_ADDRESS_LINE_2 = 'Austin, TX 78701'


def is_demo_branding_active():
    return bool(getattr(settings, 'DEMO_MODE', False))


def get_demo_brand_name():
    return (getattr(settings, 'DEMO_BRAND_NAME', None) or DEFAULT_BRAND_NAME).strip() or DEFAULT_BRAND_NAME


def get_demo_brand_legal_name():
    return (
        getattr(settings, 'DEMO_BRAND_LEGAL_NAME', None) or DEFAULT_BRAND_LEGAL_NAME
    ).strip() or DEFAULT_BRAND_LEGAL_NAME


def get_demo_brand_tagline():
    return (
        getattr(settings, 'DEMO_BRAND_TAGLINE', None) or DEFAULT_BRAND_TAGLINE
    ).strip() or DEFAULT_BRAND_TAGLINE


def get_active_brand_name():
    if is_demo_branding_active():
        return get_demo_brand_name()
    return 'La Tortilla Grocery'


def get_active_brand_legal_name():
    if is_demo_branding_active():
        return get_demo_brand_legal_name()
    return 'La Tortilla Grocery LLC'


def get_demo_contact_context():
    """Template-safe fictitious contact block for DEMO_MODE."""
    return {
        'DEMO_PHONE_DISPLAY': DEMO_PHONE_DISPLAY,
        'DEMO_PHONE_E164': DEMO_PHONE_E164,
        'DEMO_PHONE_TEL': f'tel:{DEMO_PHONE_E164}',
        'DEMO_WHATSAPP_DISPLAY': DEMO_WHATSAPP_DISPLAY,
        'DEMO_WHATSAPP_E164': DEMO_WHATSAPP_E164,
        'DEMO_WHATSAPP_URL': f'https://wa.me/{DEMO_WHATSAPP_E164}',
        'DEMO_EMAIL': DEMO_EMAIL,
        'DEMO_ADDRESS_LINE_1': DEMO_ADDRESS_LINE_1,
        'DEMO_ADDRESS_LINE_2': DEMO_ADDRESS_LINE_2,
    }


def get_demo_home_contenido_defaults():
    """CMS fields for the public home — Zyntra / Software Lab copy only."""
    brand = get_demo_brand_name()
    return {
        'hero_titulo_principal': 'Tu plataforma B2B de',
        'hero_titulo_principal_en': 'Your B2B platform for',
        'hero_titulo_resaltado': 'Operaciones mayoristas',
        'hero_titulo_resaltado_en': 'Wholesale Ops',
        'hero_titulo_final': 'lista para demostrar',
        'hero_titulo_final_en': 'ready to demo',
        'hero_subtitulo': (
            'Pedidos, inventario, fulfillment y QuickBooks en un solo lugar. '
            'Datos ficticios del Software Lab — no es producción.'
        ),
        'hero_subtitulo_en': (
            'Orders, inventory, fulfillment and QuickBooks in one place. '
            'Software Lab fictitious data — not production.'
        ),
        'hero_boton_texto': 'Ver catálogo demo',
        'hero_boton_texto_en': 'View demo catalog',
        'cta_titulo': '¿Quieres evaluar Zyntra? Abre una cuenta demo o explora el catálogo.',
        'cta_titulo_en': 'Evaluating Zyntra? Open a demo account or browse the catalog.',
        'cta_boton_registro_texto': 'Crear cuenta demo',
        'cta_boton_registro_texto_en': 'Create demo account',
        'cta_boton_catalogo_texto': 'Ver catálogo',
        'cta_boton_catalogo_texto_en': 'View catalog',
        'quienes_titulo': 'Quiénes somos',
        'quienes_titulo_en': 'Who We Are',
        'quienes_descripcion': (
            f'{brand} es una plataforma de operaciones de distribución mayorista para equipos B2B. '
            'Centraliza catálogo, inventario, pedidos, picking, facturación y sincronización contable '
            'con QuickBooks. Este entorno del Software Lab usa solo datos ficticios para demos y capacitación.'
        ),
        'quienes_descripcion_en': (
            f'{brand} is a wholesale distribution operations platform for modern B2B teams. '
            'Manage catalog, inventory, orders, fulfillment, invoicing and QuickBooks from one place — '
            'built for demos, training and Software Lab evaluations with fictitious data only.'
        ),
        'beneficio_1_titulo': 'Catálogo unificado',
        'beneficio_1_titulo_en': 'Unified catalog',
        'beneficio_1_subtitulo': 'SKUs, marcas y packs en un solo workspace',
        'beneficio_1_subtitulo_en': 'SKUs, brands and packs in one workspace',
        'beneficio_2_titulo': 'Del pedido a la factura',
        'beneficio_2_titulo_en': 'Order to invoice',
        'beneficio_2_subtitulo': 'Flujo guiado sin saltar entre herramientas',
        'beneficio_2_subtitulo_en': 'Guided flow without jumping between tools',
        'beneficio_3_titulo': 'Inventario en vivo',
        'beneficio_3_titulo_en': 'Live inventory',
        'beneficio_3_subtitulo': 'Señales para picking y compras',
        'beneficio_3_subtitulo_en': 'Signals for picking and purchasing',
        'beneficio_4_titulo': 'Listo para QuickBooks',
        'beneficio_4_titulo_en': 'QuickBooks ready',
        'beneficio_4_subtitulo': 'Sync sandbox mock para demos',
        'beneficio_4_subtitulo_en': 'Sandbox mock sync for demos',
        'estadistica_1_valor': '+48',
        'estadistica_1_valor_en': '+48',
        'estadistica_1_label': 'Cuentas demo',
        'estadistica_1_label_en': 'Demo accounts',
        'estadistica_2_valor': '99%',
        'estadistica_2_valor_en': '99%',
        'estadistica_2_label': 'Uptime de sync (demo)',
        'estadistica_2_label_en': 'Sync uptime (demo)',
        'estadistica_3_valor': '12',
        'estadistica_3_valor_en': '12',
        'estadistica_3_label': 'Módulos operativos',
        'estadistica_3_label_en': 'Operating modules',
        'footer_empresa_titulo': brand,
        'footer_empresa_titulo_en': brand,
        'footer_empresa_descripcion': (
            f'{brand} es una plataforma B2B de operaciones de distribución para demos del Software Lab. '
            'Catálogo, pedidos, inventario, fulfillment y QuickBooks con datos 100% ficticios.'
        ),
        'footer_empresa_descripcion_en': (
            f'{brand} is a B2B distribution operations platform for Software Lab demos. '
            'Catalog, orders, inventory, fulfillment and QuickBooks with 100% fictitious data.'
        ),
        'footer_contacto_titulo': 'Contacto demo',
        'footer_contacto_titulo_en': 'Demo contact',
        'footer_contacto_direccion_linea_1': DEMO_ADDRESS_LINE_1,
        'footer_contacto_direccion_linea_2': DEMO_ADDRESS_LINE_2,
        'footer_contacto_email': DEMO_EMAIL,
        'footer_contacto_telefono': DEMO_PHONE_DISPLAY,
    }


def apply_demo_home_contenido(contenido, *, save=False):
    """Overwrite HomeContenido fields with Zyntra Software Lab copy."""
    if contenido is None or not is_demo_branding_active():
        return contenido
    for field_name, value in get_demo_home_contenido_defaults().items():
        setattr(contenido, field_name, value)
    if save and getattr(contenido, 'pk', None):
        contenido.save(update_fields=list(get_demo_home_contenido_defaults().keys()) + ['actualizado'])
    elif save:
        contenido.save()
    return contenido
