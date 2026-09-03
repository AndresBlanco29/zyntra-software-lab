"""Template context shared across the project."""


def demo_environment(request):
    """Expose DEMO / Software Lab / Zyntra flags to templates (no secrets)."""
    from django.conf import settings

    from config.core.demo_branding import (
        get_active_brand_legal_name,
        get_active_brand_name,
        get_demo_brand_tagline,
        get_demo_contact_context,
    )

    demo_mode = bool(getattr(settings, 'DEMO_MODE', False))
    demo_embed = bool(getattr(request, 'demo_embed', False))
    context = {
        'DEMO_MODE': demo_mode,
        'DEMO_EMBED': demo_embed,
        'DEMO_ENVIRONMENT_LABEL': getattr(settings, 'DEMO_ENVIRONMENT_LABEL', 'SOFTWARE LAB'),
        'SHOWCASE_MODE': bool(getattr(settings, 'SHOWCASE_MODE', False)),
        'QUICKBOOKS_PROVIDER': getattr(settings, 'QUICKBOOKS_PROVIDER', 'live'),
        'DEMO_BRAND_NAME': getattr(settings, 'DEMO_BRAND_NAME', 'Zyntra'),
        'DEMO_BRAND_LEGAL_NAME': getattr(settings, 'DEMO_BRAND_LEGAL_NAME', 'Zyntra'),
        'DEMO_BRAND_TAGLINE': get_demo_brand_tagline() if demo_mode else '',
        'ACTIVE_BRAND_NAME': get_active_brand_name(),
        'ACTIVE_BRAND_LEGAL_NAME': get_active_brand_legal_name(),
    }
    if demo_mode:
        context.update(get_demo_contact_context())
    return context
