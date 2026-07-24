from config.notificaciones.alerts import (
    get_customer_request_alerts,
    get_urgent_workspace_alerts,
)


def workspace_urgent_alerts(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {
            'workspace_urgent_alerts': None,
            'workspace_customer_request_alerts': None,
        }
    return {
        'workspace_urgent_alerts': get_urgent_workspace_alerts(request.user),
        'workspace_customer_request_alerts': get_customer_request_alerts(request.user),
    }
