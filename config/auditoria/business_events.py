import logging

from django.utils.translation import gettext as _

from config.auditoria.enrichment import (
    geo_hint_from_ip,
    normalize_changes,
    parse_user_agent,
    resolve_module,
)
from config.auditoria.models import AuditLog

logger = logging.getLogger(__name__)

QUICKBOOKS_IMPORT_OPERATIONS = {
    'import_customers',
    'import_items',
    'import_customers_to_local',
    'import_items_to_local',
    'import_invoices',
    'import_credit_memos',
    'import_accounting_documents_to_local',
    'pull_sync_to_local',
    'pull_items_sync_to_local',
    'refresh_linked_items_to_local',
    'refresh_linked_invoice_status_to_local',
}

QUICKBOOKS_EXPORT_OPERATIONS = {
    'sync_customer',
    'sync_product',
    'sync_invoice',
    'sync_adjustment_note',
    'sync_customers_batch',
    'sync_products_batch',
    'sync_invoices_batch',
    'sync_adjustment_notes_batch',
    'sync_supplier_purchases_batch',
    'push_linked_products_to_quickbooks',
    'push_linked_products_batch',
}


def _actor_fields(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return {
            'actor': None,
            'actor_username': '',
            'actor_full_name': '',
            'actor_role': '',
        }
    full_name = (user.get_full_name() or user.username or '')[:255]
    return {
        'actor': user,
        'actor_username': (getattr(user, 'username', '') or '')[:150],
        'actor_full_name': full_name,
        'actor_role': (getattr(user, 'role', '') or '')[:30],
    }


def _request_fields(request):
    if request is None:
        return {
            'http_method': '',
            'path': '',
            'route_name': '',
            'ip_address': None,
            'user_agent': '',
            'browser': '',
            'os_name': '',
            'device': '',
            'geo_city': '',
            'geo_country': '',
            'status_code': 200,
            'duration_ms': None,
        }

    from config.auditoria.services import _client_ip, _duration_ms_from_request, _resolve_route_name, _truncate

    ua = _truncate(request.META.get('HTTP_USER_AGENT', ''), 500)
    ua_parts = parse_user_agent(ua)
    ip_address = _client_ip(request)
    geo = geo_hint_from_ip(ip_address)
    return {
        'http_method': _truncate(request.method, 10),
        'path': _truncate(request.path, 500),
        'route_name': _truncate(_resolve_route_name(request), 120),
        'ip_address': ip_address,
        'user_agent': ua,
        'browser': ua_parts['browser'],
        'os_name': ua_parts['os_name'],
        'device': ua_parts['device'],
        'geo_city': geo['geo_city'],
        'geo_country': geo['geo_country'],
        'status_code': 200,
        'duration_ms': _duration_ms_from_request(request),
    }


def log_business_event(
    user,
    *,
    action_label,
    action_category=AuditLog.CATEGORY_ACTION,
    entity_type='',
    entity_id='',
    entity_label='',
    metadata=None,
    changes=None,
    request=None,
    status_code=200,
    module='',
):
    try:
        fields = _actor_fields(user)
        request_data = _request_fields(request)
        if status_code != 200:
            request_data['status_code'] = status_code
        meta = metadata or {}
        resolved_changes = normalize_changes(changes, meta if isinstance(meta, dict) else {})
        resolved_module = module or resolve_module(
            route_name=request_data.get('route_name', ''),
            path=request_data.get('path', ''),
            entity_type=entity_type,
        )
        return AuditLog.objects.create(
            **fields,
            action_category=action_category,
            action_label=str(action_label)[:255],
            entity_type=str(entity_type or '')[:80],
            entity_id=str(entity_id or '')[:80],
            entity_label=str(entity_label or '')[:255],
            module=str(resolved_module or '')[:80],
            success=int(status_code) < 400,
            changes=resolved_changes,
            metadata=meta,
            **request_data,
        )
    except Exception:
        logger.exception('Failed to persist business audit event')
        return None


def _summarize_quickbooks_result(result):
    result = result or {}
    if isinstance(result.get('results'), list):
        return {
            'created_count': result.get('created_count', 0),
            'updated_count': result.get('updated_count', 0),
            'failed_count': result.get('failed_count', 0),
            'conflict_count': result.get('conflict_count', 0),
            'linked_count': result.get('linked_count', result.get('count', 0)),
        }
    nested = {}
    for key in ('customers', 'items', 'accounting_documents'):
        if key in result and isinstance(result[key], dict):
            nested[key] = {
                'created_count': result[key].get('created_count', 0),
                'updated_count': result[key].get('updated_count', 0),
                'conflict_count': result[key].get('conflict_count', 0),
            }
    if nested:
        return nested
    if result.get('quickbooks_id'):
        return {'quickbooks_id': result.get('quickbooks_id'), 'action': result.get('action')}
    if result.get('label'):
        return {'label': result.get('label'), 'action': result.get('action')}
    return result


def log_quickbooks_operation(request, *, operation, result=None, error=None):
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return None

    operation_key = (operation or '').strip()
    if operation_key in QUICKBOOKS_IMPORT_OPERATIONS:
        category = AuditLog.CATEGORY_SYNC
        direction = _('Import from QuickBooks')
    elif operation_key in QUICKBOOKS_EXPORT_OPERATIONS:
        category = AuditLog.CATEGORY_EXPORT
        direction = _('Export to QuickBooks')
    else:
        category = AuditLog.CATEGORY_SYNC
        direction = _('QuickBooks operation')

    if error:
        action_label = _('%(direction)s failed: %(operation)s') % {
            'direction': direction,
            'operation': operation_key.replace('_', ' '),
        }
        metadata = {'operation': operation_key, 'error': str(error)}
        status_code = 502
    else:
        summary = _summarize_quickbooks_result(result)
        action_label = _('%(direction)s: %(operation)s') % {
            'direction': direction,
            'operation': operation_key.replace('_', ' '),
        }
        metadata = {'operation': operation_key, 'result': summary}
        status_code = 200

    entity_label = ''
    if isinstance(result, dict):
        entity_label = str(result.get('label') or result.get('quickbooks_id') or '')

    return log_business_event(
        user,
        action_label=action_label,
        action_category=category,
        entity_type='QuickBooks',
        entity_id=str(result.get('quickbooks_id') or '')[:80] if isinstance(result, dict) else '',
        entity_label=entity_label[:255],
        metadata=metadata,
        request=request,
        status_code=status_code,
        module='QuickBooks',
    )
