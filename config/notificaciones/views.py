from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import formats
from django.utils.timezone import localtime
from django.views.decorators.http import require_GET, require_POST

from config.usuarios.permissions import internal_permission_required

from .alerts import (
    get_customer_request_alerts,
    get_urgent_workspace_alerts,
    mark_customer_request_alerts_seen,
    mark_dispatch_alerts_seen,
)


def _serialize_datetime(value):
    if value is None:
        return ''
    local_value = localtime(value)
    return formats.date_format(local_value, 'm/d/Y H:i')


@login_required
@require_POST
@internal_permission_required('backoffice.orders.view', 'backoffice.quotes.view')
def mark_dispatch_alerts_seen_view(request):
    mark_dispatch_alerts_seen(request.user)
    return JsonResponse({'success': True, 'unread_count': 0})


@login_required
@require_GET
@internal_permission_required('backoffice.orders.view', 'backoffice.quotes.view')
def dispatch_alerts_feed_view(request):
    alerts = get_urgent_workspace_alerts(request.user) or {}
    return JsonResponse({
        'success': True,
        'total_count': int(alerts.get('total_count') or 0),
        'summary_items': alerts.get('summary_items') or [],
        'recent_items': [
            {
                'title': item.get('title') or '',
                'message': item.get('message') or '',
                'url': item.get('url') or '',
                'is_unread': bool(item.get('is_unread')),
            }
            for item in (alerts.get('recent_items') or [])
        ],
        'orders_url': alerts.get('orders_url') or '',
    })


@login_required
@require_POST
@internal_permission_required('admin.customer_requests.view')
def mark_customer_request_alerts_seen_view(request):
    mark_customer_request_alerts_seen(request.user)
    alerts = get_customer_request_alerts(request.user) or {}
    return JsonResponse({'success': True, 'pending_count': int(alerts.get('pending_count') or 0)})


@login_required
@require_GET
@internal_permission_required('admin.customer_requests.view')
def customer_request_alerts_feed_view(request):
    alerts = get_customer_request_alerts(request.user) or {}
    items = []
    for item in alerts.get('recent_items') or []:
        items.append({
            'id': item.get('id'),
            'customer_name': item.get('customer_name') or '',
            'company': item.get('company') or '',
            'email': item.get('email') or '',
            'registered_at': _serialize_datetime(item.get('registered_at')),
            'url': item.get('url') or '',
            'approve_url': item.get('approve_url') or '',
            'reject_url': item.get('reject_url') or '',
            'is_unread': bool(item.get('is_unread')),
        })
    return JsonResponse({
        'success': True,
        'pending_count': int(alerts.get('pending_count') or 0),
        'items': items,
        'list_url': alerts.get('list_url') or '',
        'can_manage': bool(alerts.get('can_manage')),
    })
