from django import template
from django.contrib.staticfiles.storage import staticfiles_storage


register = template.Library()


@register.simple_tag
def safe_static(path):
    try:
        return staticfiles_storage.url(path)
    except ValueError:
        return ""
