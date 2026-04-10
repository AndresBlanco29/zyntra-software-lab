from django import template

from config.usuarios.permissions import user_has_permission


register = template.Library()


@register.filter(name='has_internal_permission')
def has_internal_permission(user, permission_code):
    return user_has_permission(user, permission_code)