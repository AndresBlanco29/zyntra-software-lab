from datetime import date, datetime

from django.utils import timezone

APP_DATE_STRFTIME = '%m/%d/%Y'
APP_DATETIME_STRFTIME = '%m/%d/%Y %H:%M'
APP_DATETIME_SECONDS_STRFTIME = '%m/%d/%Y %H:%M:%S'

DJANGO_DATE_FORMAT = 'm/d/Y'
DJANGO_DATETIME_FORMAT = 'm/d/Y H:i'
DJANGO_DATETIME_SECONDS_FORMAT = 'm/d/Y H:i:s'


def format_local_date(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return timezone.localtime(value).date().strftime(APP_DATE_STRFTIME)
    if isinstance(value, date):
        return value.strftime(APP_DATE_STRFTIME)
    return ''


def format_local_datetime(value, *, seconds=False):
    if value is None:
        return ''
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime(APP_DATE_STRFTIME)
    if isinstance(value, datetime):
        local_value = timezone.localtime(value) if timezone.is_aware(value) else value
        pattern = APP_DATETIME_SECONDS_STRFTIME if seconds else APP_DATETIME_STRFTIME
        return local_value.strftime(pattern)
    return ''
