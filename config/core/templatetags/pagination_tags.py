from django import template

from config.core.pagination import get_quick_jump_pages

register = template.Library()


@register.inclusion_tag('includes/pagination_controls.html')
def pagination_controls(page_obj, aria_label='', nav_class='', **query_params):
    cleaned_params = {
        key: str(value)
        for key, value in query_params.items()
        if value not in (None, '')
    }
    return {
        'page_obj': page_obj,
        'jump_pages': get_quick_jump_pages(page_obj.number, page_obj.paginator.num_pages),
        'query_params': cleaned_params,
        'aria_label': aria_label,
        'nav_class': nav_class,
    }
