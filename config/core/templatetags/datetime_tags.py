from django import template

from config.core.datetime_formats import format_local_date, format_local_datetime

register = template.Library()


@register.filter
def app_date(value):
    formatted = format_local_date(value)
    return formatted or '-'


@register.filter
def app_datetime(value):
    formatted = format_local_datetime(value)
    return formatted or '-'


@register.filter
def app_datetime_seconds(value):
    formatted = format_local_datetime(value, seconds=True)
    return formatted or '-'
