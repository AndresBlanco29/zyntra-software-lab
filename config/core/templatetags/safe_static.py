from django import template
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage


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
        static_path = staticfiles_storage.url(path)
    except ValueError:
        return ""

    if static_path.startswith('http://') or static_path.startswith('https://'):
        return static_path

    app_base_url = getattr(settings, 'APP_BASE_URL', '').rstrip('/')
    if not app_base_url:
        return static_path

    if not static_path.startswith('/'):
        static_path = f'/{static_path}'
    return f'{app_base_url}{static_path}'
