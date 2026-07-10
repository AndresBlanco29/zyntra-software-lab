from django import template
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage

from config.core.email_branding import build_absolute_static_url, get_app_base_url


register = template.Library()


@register.simple_tag
def safe_static(path):
    try:
        return staticfiles_storage.url(path)
    except ValueError:
        return ""


@register.simple_tag
def absolute_safe_static(path):
    try:
        return build_absolute_static_url(path)
    except Exception:
        app_base_url = get_app_base_url() or getattr(settings, 'APP_BASE_URL', '').rstrip('/')
        static_path = f"{settings.STATIC_URL.rstrip('/')}/{str(path).lstrip('/')}"
        if not app_base_url:
            return static_path
        return f'{app_base_url}{static_path}'
