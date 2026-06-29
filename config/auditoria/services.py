import logging
import re

from django.utils.translation import gettext as _

from config.auditoria.models import AuditLog

logger = logging.getLogger(__name__)

SKIP_PATH_PREFIXES = (
    '/static/',
    '/media/',
    '/i18n/',
    '/favicon.ico',
    '/sitemap.xml',
)

SKIP_ROUTE_NAMES = {
    'driver_delivery_update_location',
    'quickbooks_task_status',
    'backup_restore_status',
}

ROUTE_LABELS = {
    'panel_admin': _('Admin dashboard'),
    'reportes_dashboard': _('Reports Center'),
    'lista_usuarios_internos': _('Internal users list'),
    'crear_usuario_interno': _('Create internal user'),
    'editar_usuario_interno': _('Edit internal user'),
    'lista_productos': _('Products list'),
    'crear_producto': _('Create product'),
    'editar_producto': _('Edit product'),
    'clientes': _('Customers'),
    'crear_cliente': _('Create customer'),
    'tomar_pedido': _('Take order'),
    'backoffice_pedidos_list': _('Sales orders'),
    'backoffice_pedido_detalle': _('Sales order detail'),
    'backoffice_invoices_list': _('Invoices'),
    'backoffice_invoice_detail': _('Invoice detail'),
    'quickbooks_center': _('QuickBooks Center'),
    'driver_delivery_list': _('Driver deliveries'),
    'driver_delivery_detail': _('Driver delivery detail'),
    'audit_log_list': _('Audit trail'),
    'login': _('Sign in'),
    'logout': _('Sign out'),
}


def is_admin_user(user):
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and (getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == 'admin')
    )


def should_skip_audit_request(request):
    path = (request.path or '').lower()
    for prefix in SKIP_PATH_PREFIXES:
        if path.startswith(prefix):
            return True

    resolver = getattr(request, 'resolver_match', None)
    route_name = getattr(resolver, 'url_name', '') or ''
    if route_name in SKIP_ROUTE_NAMES:
        return True

    return False


def _client_ip(request):
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    if forwarded:
        return forwarded[:45]
    return (request.META.get('REMOTE_ADDR') or '')[:45] or None


def _truncate(value, limit):
    text = str(value or '').strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + '...'


def _resolve_route_name(request):
    resolver = getattr(request, 'resolver_match', None)
    return getattr(resolver, 'url_name', '') or ''


def _resolve_action_label(request):
    route_name = _resolve_route_name(request)
    if route_name in ROUTE_LABELS:
        return str(ROUTE_LABELS[route_name])

    if route_name:
        return route_name.replace('_', ' ').strip().title()

    return _truncate(request.path, 255)


def _resolve_action_category(request):
    method = (request.method or 'GET').upper()
    route_name = _resolve_route_name(request).lower()
    path = (request.path or '').lower()

    if route_name in {'login', 'login_modal'} or path.endswith('/login/'):
        return AuditLog.CATEGORY_LOGIN
    if route_name == 'logout' or 'logout' in path:
        return AuditLog.CATEGORY_LOGOUT
    if 'export' in route_name or path.endswith('/export/') or '/export/' in path:
        return AuditLog.CATEGORY_EXPORT
    if route_name.startswith('quickbooks_') and method == 'POST':
        return AuditLog.CATEGORY_SYNC

    if method == 'GET':
        return AuditLog.CATEGORY_VIEW
    if method == 'DELETE':
        return AuditLog.CATEGORY_DELETE
    if method in {'PUT', 'PATCH'}:
        return AuditLog.CATEGORY_UPDATE
    if method == 'POST':
        if any(token in route_name for token in ('create', 'crear', 'add', 'register', 'signup')):
            return AuditLog.CATEGORY_CREATE
        if any(token in route_name for token in ('edit', 'editar', 'update', 'manage', 'save')):
            return AuditLog.CATEGORY_UPDATE
        if any(token in route_name for token in ('delete', 'eliminar', 'void', 'anular')):
            return AuditLog.CATEGORY_DELETE
        return AuditLog.CATEGORY_ACTION
    return AuditLog.CATEGORY_ACTION


def _build_metadata(request, response):
    metadata = {
        'query': request.GET.dict() if request.method == 'GET' else {},
    }
    if request.method == 'POST':
        post_data = {}
        for key, value in request.POST.items():
            if key.lower() in {'password', 'password1', 'password2', 'csrfmiddlewaretoken'}:
                post_data[key] = '[redacted]'
            elif isinstance(value, str) and len(value) > 180:
                post_data[key] = value[:180] + '...'
            else:
                post_data[key] = value
        metadata['post'] = post_data

    if response is not None and getattr(response, 'status_code', None):
        metadata['status_code'] = response.status_code
    return metadata


def record_audit_event(
    request,
    *,
    response=None,
    action_label=None,
    action_category=None,
    entity_type='',
    entity_id='',
    entity_label='',
    metadata=None,
):
    user = getattr(request, 'user', None)
    route_name = _resolve_route_name(request)
    is_login_attempt = action_category == AuditLog.CATEGORY_LOGIN or route_name in {'login', 'login_modal'}

    if not user or not getattr(user, 'is_authenticated', False):
        if not is_login_attempt:
            return None

    try:
        actor = user if getattr(user, 'is_authenticated', False) else None
        log = AuditLog.objects.create(
            actor=actor,
            actor_username=_truncate(getattr(actor, 'username', '') if actor else request.POST.get('username', ''), 150),
            actor_role=_truncate(getattr(actor, 'role', '') if actor else '', 30),
            action_category=action_category or _resolve_action_category(request),
            action_label=_truncate(action_label or _resolve_action_label(request), 255),
            http_method=_truncate(request.method, 10),
            path=_truncate(request.path, 500),
            route_name=_truncate(route_name, 120),
            ip_address=_client_ip(request),
            user_agent=_truncate(request.META.get('HTTP_USER_AGENT', ''), 500),
            entity_type=_truncate(entity_type, 80),
            entity_id=_truncate(entity_id, 80),
            entity_label=_truncate(entity_label, 255),
            status_code=getattr(response, 'status_code', 200) if response is not None else 200,
            metadata=metadata if metadata is not None else _build_metadata(request, response),
        )
        return log
    except Exception:
        logger.exception('Failed to persist audit log entry')
        return None


def record_audit_event_from_request(request, response):
    if should_skip_audit_request(request):
        return None

    user = getattr(request, 'user', None)
    route_name = _resolve_route_name(request)
    method = (request.method or 'GET').upper()
    is_login = route_name in {'login', 'login_modal'} or method == 'POST' and '/login' in (request.path or '')

    if user and getattr(user, 'is_authenticated', False):
        if method == 'GET' and getattr(user, 'role', '') == 'cliente':
            if not re.search(r'/clientes/|/cotizaciones/|/carrito/|/pedidos/', request.path or '', re.I):
                return None
    elif not is_login:
        return None

    return record_audit_event(request, response=response)
