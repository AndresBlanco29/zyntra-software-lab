from django.urls import reverse
from django.utils.translation import gettext as _


def _is_internal_panel_user(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(user, 'role', None) != 'cliente'


def _append_alert(items, *, label, detail, url, count, priority='medium', kind='general'):
    count = int(count or 0)
    if count <= 0:
        return
    items.append({
        'label': label,
        'detail': detail,
        'url': url,
        'count': count,
        'priority': priority,
        'kind': kind,
    })


def get_urgent_workspace_alerts(user):
    if not _is_internal_panel_user(user):
        return None
    if not user.has_internal_permission('backoffice.quotes.view'):
        return None

    from config.cotizaciones.models import Cotizacion

    base_queryset = Cotizacion.objects.select_related('cliente').order_by('-fecha')
    quotes_url = reverse('backoffice_cotizaciones')
    summary_items = []

    pending_review = base_queryset.filter(estado='ENVIADA').count()
    _append_alert(
        summary_items,
        label=_('Pending review'),
        detail=_('New quotes sent by customers waiting for BackOffice'),
        url=quotes_url,
        count=pending_review,
        priority='high',
        kind='quotes-pending',
    )

    awaiting_customer = base_queryset.filter(estado='LISTA_PARA_CONFIRMACION').count()
    _append_alert(
        summary_items,
        label=_('Waiting for customer'),
        detail=_('Quotes sent to the customer that are still open'),
        url=quotes_url,
        count=awaiting_customer,
        priority='medium',
        kind='quotes-awaiting-customer',
    )

    confirmed_pending = base_queryset.filter(estado='CONFIRMADA_CLIENTE').count()
    _append_alert(
        summary_items,
        label=_('Confirmed, not finished'),
        detail=_('Customer confirmed the quote and BackOffice still needs to complete it'),
        url=f'{quotes_url}?view=confirmed',
        count=confirmed_pending,
        priority='high',
        kind='quotes-confirmed',
    )

    active_statuses = ['ENVIADA', 'LISTA_PARA_CONFIRMACION', 'CONFIRMADA_CLIENTE']
    recent_quotes = list(base_queryset.filter(estado__in=active_statuses)[:8])

    total_count = pending_review + awaiting_customer + confirmed_pending

    return {
        'total_count': total_count,
        'summary_items': summary_items,
        'recent_quotes': recent_quotes,
        'quotes_url': quotes_url,
    }
