from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from config.usuarios.permissions import internal_permission_required

from .alerts import mark_dispatch_alerts_seen


@login_required
@require_POST
@internal_permission_required('backoffice.orders.view')
def mark_dispatch_alerts_seen_view(request):
    mark_dispatch_alerts_seen(request.user)
    return JsonResponse({'success': True, 'unread_count': 0})
