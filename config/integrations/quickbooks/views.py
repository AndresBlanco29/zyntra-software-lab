import logging
from functools import wraps
from pathlib import Path

from django.contrib import messages
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from config.clientes.models import Cliente
from config.facturacion.models import Invoice, NotaAjuste
from config.integrations.models import QuickBooksImportConflict
from config.inventario.models import CompraProveedor, CompraProveedorLinea, Proveedor
from config.productos.models import Presentacion

from config.usuarios.permissions import get_redirect_url_for_user, internal_permission_required
from config.integrations.backups import (
    DatabaseBackupError,
    _get_backup_storage,
    create_database_backup_file,
    create_system_backup_file,
    list_database_backups,
    list_system_backups,
    open_database_backup,
    open_system_backup,
    restore_database_backup_file,
    restore_database_backup_upload,
)

from .auth import QuickBooksConfigurationError, quickbooks_credentials_configured, quickbooks_credentials_setup_message
from .client import QuickBooksAPIClient, QuickBooksAPIError
from .services import QuickBooksServiceError, get_connection, get_connection_status, get_oauth_login_url, handle_oauth_callback
from .sync import (
    dismiss_quickbooks_import_conflict,
    fetch_quickbooks_bills,
    fetch_quickbooks_vendors,
    import_quickbooks_customers,
    import_quickbooks_bills,
    import_quickbooks_vendors,
    import_quickbooks_items,
    import_quickbooks_accounting_documents,
    link_quickbooks_import_conflict,
    pull_quickbooks_to_local,
    QuickBooksSyncError,
    fetch_quickbooks_credit_memos,
    fetch_quickbooks_customers,
    fetch_quickbooks_invoices,
    fetch_quickbooks_items,
    fetch_quickbooks_purchase_orders,
    retry_quickbooks_import_conflict,
    import_quickbooks_purchase_orders,
    sync_adjustment_note_batch_by_ids,
    sync_adjustment_note_by_id,
    sync_customer_batch_by_ids,
    sync_customer_by_id,
    sync_invoice_batch_by_ids,
    sync_invoice_by_id,
    sync_product_batch_by_ids,
    sync_product_by_id,
    sync_supplier_purchase,
)
import threading
import uuid
import time
from django.core.cache import cache


logger = logging.getLogger(__name__)

CATALOG_ONLY_BLOCKED_MESSAGE = _(
    'This QuickBooks action is disabled while catalog-only mode is active. '
    'Only catalog preview and import are enabled.'
)


def _quickbooks_catalog_only_enabled():
    return getattr(settings, 'QUICKBOOKS_CATALOG_ONLY_MODE', True)


def _guard_quickbooks_catalog_only(request, *, operation='quickbooks'):
    if not _quickbooks_catalog_only_enabled():
        return None
    return _response_or_redirect(request, operation=operation, error=CATALOG_ONLY_BLOCKED_MESSAGE, status_code=403)


def quickbooks_requires_full_mode(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        blocked = _guard_quickbooks_catalog_only(request, operation=view_func.__name__)
        if blocked is not None:
            return blocked
        return view_func(request, *args, **kwargs)

    return wrapper


BACKUP_SCHEDULE_CHOICES = (
    ('daily', 'Daily'),
    ('weekly', 'Weekly'),
    ('monthly', 'Monthly'),
)
DEFAULT_BACKUP_SCHEDULE = 'weekly'


def _format_backup_size(size_bytes):
    size = float(size_bytes or 0)
    units = ('B', 'KB', 'MB', 'GB')
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f'{size:.1f} {units[unit_index]}'


def _normalize_backup_schedule(value):
    raw_value = str(value or '').strip().lower()
    valid_values = {choice[0] for choice in BACKUP_SCHEDULE_CHOICES}
    return raw_value if raw_value in valid_values else DEFAULT_BACKUP_SCHEDULE


def _backup_schedule_label(value):
    normalized_value = _normalize_backup_schedule(value)
    labels = {key: label for key, label in BACKUP_SCHEDULE_CHOICES}
    return labels.get(normalized_value, labels[DEFAULT_BACKUP_SCHEDULE])


def _get_backup_schedule_preference():
    connection = get_connection()
    raw_state = connection.sync_state if isinstance(connection.sync_state, dict) else {}
    return _normalize_backup_schedule(raw_state.get('backup_schedule'))


def _set_backup_schedule_preference(schedule):
    normalized_schedule = _normalize_backup_schedule(schedule)
    connection = get_connection()
    state = dict(connection.sync_state or {})
    state['backup_schedule'] = normalized_schedule
    connection.sync_state = state
    connection.save(update_fields=['sync_state', 'updated_at'])
    return normalized_schedule


def _build_backup_schedule_options(selected_value):
    normalized_value = _normalize_backup_schedule(selected_value)
    return [
        {
            'value': value,
            'label': label,
            'selected': value == normalized_value,
        }
        for value, label in BACKUP_SCHEDULE_CHOICES
    ]


def _build_backup_history(limit=8):
    history = []
    for backup in list_database_backups(limit=limit):
        history.append({
            'name': backup['name'],
            'size_label': _format_backup_size(backup['size_bytes']),
            'modified_label': date_format(timezone.localtime(backup['modified_at']), 'Y-m-d H:i'),
            'download_url_name': 'quickbooks_database_backup_download',
        })
    return history


def _build_system_backup_history(limit=8):
    history = []
    for backup in list_system_backups(limit=limit):
        history.append({
            'name': backup['name'],
            'size_label': _format_backup_size(backup['size_bytes']),
            'modified_label': date_format(timezone.localtime(backup['modified_at']), 'Y-m-d H:i'),
            'download_url_name': 'system_backup_download',
        })
    return history


def _build_restore_options(limit=20):
    options = []
    for backup in _build_system_backup_history(limit=limit):
        options.append({
            'name': backup['name'],
            'label': f"{backup['name']} ({backup['modified_label']} · {backup['size_label']})",
            'kind': 'system',
        })
    for backup in _build_backup_history(limit=limit):
        options.append({
            'name': backup['name'],
            'label': f"{backup['name']} ({backup['modified_label']} · {backup['size_label']})",
            'kind': 'database',
        })
    return options


def _build_database_backups_context(*, request=None):
    backup_schedule = _get_backup_schedule_preference()
    return {
        'database_restore_options': _build_restore_options(limit=20),
        'system_backup_history': _build_system_backup_history(limit=20),
        'system_backup_total': len(list_system_backups(limit=None)),
        'database_backup_history': _build_backup_history(limit=20),
        'database_backup_total': len(list_database_backups(limit=None)),
        'backup_schedule_preference': backup_schedule,
        'backup_schedule_label': _backup_schedule_label(backup_schedule),
        'backup_schedule_options': _build_backup_schedule_options(backup_schedule),
        'quickbooks_status': get_connection_status(),
    }


def _render_restore_completion(backup_name):
    return HttpResponse(
        (
            '<!doctype html><html><head><meta charset="utf-8">'
            '<title>Restore completed</title>'
            '<style>'
            'body{font-family:Segoe UI,Arial,sans-serif;background:#f4f7fb;color:#102a63;padding:32px;}'
            '.card{max-width:760px;margin:0 auto;background:#fff;border:1px solid rgba(16,42,99,.12);'
            'border-radius:24px;padding:32px;box-shadow:0 18px 40px rgba(16,42,99,.08);}'
            'h1{margin:0 0 12px;}p{line-height:1.55;color:#4f6484;}code{background:#eef4ff;padding:2px 6px;border-radius:6px;}'
            'a{color:#1f6fd1;text-decoration:none;font-weight:600;}'
            '</style></head><body><div class="card">'
            '<h1>Restore completed</h1>'
            f'<p>The system was restored from <code>{backup_name}</code>.</p>'
            '<p>If the current session was replaced during restore, sign in again before continuing.</p>'
            '<p><a href="/login/">Go to sign in</a></p>'
            '</div></body></html>'
        ),
        content_type='text/html; charset=utf-8',
    )


def _resolve_dashboard_redirect(request):
    redirect_to = str(request.POST.get('redirect_to') or request.GET.get('redirect_to') or '').strip()
    if redirect_to.startswith('/'):
        return redirect_to
    return ''


def _conflicts_redirect_target(request):
    redirect_to = _resolve_dashboard_redirect(request)
    if redirect_to:
        return redirect_to
    return request.META.get('HTTP_REFERER') or '/quickbooks/import/conflicts'


def _build_dashboard_feedback(*, operation, ok, result=None, error=None):
    feedback = {
        'operation': operation,
        'ok': ok,
        'title': operation.replace('_', ' ').title(),
        'details': [],
    }
    if error:
        feedback['details'].append(str(error))
        return feedback

    result = result or {}
    if operation == 'status':
        feedback['title'] = 'QuickBooks Status'
        if result.get('connected'):
            feedback['details'].append(f"Connected to {result.get('status', {}).get('environment', 'sandbox')}.")
            realm_id = result.get('status', {}).get('realm_id')
            if realm_id:
                feedback['details'].append(f'Realm ID: {realm_id}')
        else:
            feedback['details'].append('QuickBooks is not connected yet.')
        return feedback

    if operation == 'test_connection':
        feedback['title'] = 'QuickBooks Connection Test'
        company = result.get('company') or {}
        company_name = company.get('CompanyName') or company.get('LegalName') or company.get('Id')
        feedback['details'].append(
            f'Connected to {company_name}.' if company_name else 'Connection test completed successfully.'
        )
        return feedback

    if operation == 'import_accounting_documents_to_local':
        feedback['title'] = 'QuickBooks Accounting Pull'
        feedback['details'].append(
            f"Matched: {result.get('matched_count', 0)}. Conflicts queued: {result.get('conflict_count', 0)}."
        )
        for sample in result.get('results', [])[:3]:
            feedback['details'].append(
                f"{sample.get('label')}: linked to local record." if sample.get('ok') else sample.get('error')
            )
        return feedback

    if operation == 'pull_sync_to_local':
        feedback['title'] = 'QuickBooks Pull Sync'
        customers = result.get('customers', {})
        vendors = result.get('vendors', {})
        items = result.get('items', {})
        accounting = result.get('accounting_documents', {})
        bills = result.get('bills', {})
        feedback['details'].append(
            f"Customers -> created {customers.get('created_count', 0)}, updated {customers.get('updated_count', 0)}, conflicts {customers.get('conflict_count', 0)}."
        )
        feedback['details'].append(
            f"Suppliers -> created {vendors.get('created_count', 0)}, updated {vendors.get('updated_count', 0)}, conflicts {vendors.get('conflict_count', 0)}."
        )
        feedback['details'].append(
            f"Catalog -> created {items.get('created_count', 0)}, updated {items.get('updated_count', 0)}, conflicts {items.get('conflict_count', 0)}."
        )
        feedback['details'].append(
            f"Accounting docs -> matched {accounting.get('matched_count', 0)}, conflicts queued {accounting.get('conflict_count', 0)}."
        )
        feedback['details'].append(
            _('Bills -> created %(created)s, updated %(updated)s, conflicts %(conflicts)s.') % {
                'created': bills.get('created_count', 0),
                'updated': bills.get('updated_count', 0),
                'conflicts': bills.get('conflict_count', 0),
            }
        )
        feedback['details'].append(
            _('Incremental sync used saved cursors.') if result.get('incremental') else _('Full sync ignored saved cursors.')
        )
        return feedback

    if operation.startswith('import_'):
        if 'created_count' in result or 'updated_count' in result:
            entity_label = result.get('entity', preview_key.title() if 'preview_key' in locals() else 'Records')
            feedback['title'] = f'QuickBooks {entity_label} Import'
            feedback['details'].append(
                f"Created: {result.get('created_count', 0)}. Updated: {result.get('updated_count', 0)}. Conflicts: {result.get('conflict_count', 0)}. Failed: {result.get('failed_count', 0)}."
            )
            for sample in result.get('results', [])[:3]:
                if sample.get('ok'):
                    feedback['details'].append(f"{sample.get('label')}: {sample.get('action')}")
                elif sample.get('action') == 'conflict':
                    feedback['details'].append(sample.get('error'))
            return feedback
        preview_key = operation.split('_', 1)[1]
        feedback['title'] = f'QuickBooks {preview_key.title()} Preview'
        feedback['details'].append(f"{result.get('count', 0)} record(s) available in the preview.")
        for sample in result.get(preview_key, [])[:3]:
            label = sample.get('DisplayName') or sample.get('Name') or sample.get('DocNumber') or sample.get('Id')
            if label:
                feedback['details'].append(str(label))
        return feedback

    if 'requested_ids' in result:
        feedback['details'].append(
            f"Succeeded: {result.get('success_count', 0)}. Failed: {result.get('failed_count', 0)}."
        )
        for failed_item in [item for item in result.get('results', []) if not item.get('ok')][:3]:
            feedback['details'].append(f"ID {failed_item.get('id')}: {failed_item.get('error')}")
        return feedback

    action = result.get('action')
    entity = result.get('entity')
    quickbooks_id = result.get('quickbooks_id')
    if entity or action:
        feedback['details'].append(f"{entity or 'Record'} {action or 'processed'} successfully.")
    else:
        feedback['details'].append('Operation completed successfully.')
    if quickbooks_id:
        feedback['details'].append(f'QuickBooks ID: {quickbooks_id}')
    return feedback


def _response_or_redirect(request, *, operation, result=None, error=None, status_code=200):
    redirect_to = _resolve_dashboard_redirect(request)
    if redirect_to:
        feedback = _build_dashboard_feedback(operation=operation, ok=error is None, result=result, error=error)
        request.session['quickbooks_dashboard_feedback'] = feedback
        if error is None:
            messages.success(request, feedback['details'][0] if feedback['details'] else 'QuickBooks operation completed successfully.')
        else:
            messages.error(request, str(error))
        return redirect(redirect_to)
    return _sync_payload(operation=operation, result=result, error=error, status_code=status_code)


def _status_payload(*, connection_status, connected=False, company=None, error=None):
    payload = {
        'connected': connected,
        'status': connection_status,
    }
    if company is not None:
        payload['company'] = company
    if error:
        payload['error'] = error
    return payload


def _sync_payload(*, operation, result=None, error=None, status_code=200):
    payload = {
        'ok': error is None,
        'operation': operation,
        'status': get_connection_status(),
    }
    if result is not None:
        payload['result'] = result
    if error is not None:
        payload['error'] = error
    return JsonResponse(payload, status=status_code)


def _parse_id_list(raw_value):
    values = []
    seen = set()
    for chunk in str(raw_value or '').replace('\n', ',').split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.isdigit():
            raise ValueError(f'Invalid ID value: {chunk}')
        numeric_value = int(chunk)
        if numeric_value not in seen:
            seen.add(numeric_value)
            values.append(numeric_value)
    if not values:
        raise ValueError('Provide at least one valid ID.')
    return values


def get_dashboard_sync_context(*, request=None):
    feedback = None
    if request is not None:
        feedback = request.session.pop('quickbooks_dashboard_feedback', None)
    pending_customers = Cliente.objects.filter(quickbooks_id__isnull=True).order_by('-id')
    recent_suppliers = Proveedor.objects.order_by('-actualizado_en', '-id')
    pending_presentations = Presentacion.objects.filter(quickbooks_id__isnull=True).select_related('producto').order_by('-id')
    pending_invoices = Invoice.objects.filter(quickbooks_id__isnull=True).select_related('cliente').order_by('-id')
    pending_notes = NotaAjuste.objects.filter(quickbooks_id__isnull=True, estado='APROBADA').select_related('cliente', 'invoice').order_by('-id')
    recent_supplier_purchases = CompraProveedor.objects.prefetch_related('lineas__presentacion__producto').order_by('-creado_en', '-id')
    return {
        'quickbooks_pending_customers': pending_customers[:10],
        'quickbooks_recent_suppliers': recent_suppliers[:8],
        'quickbooks_pending_presentations': pending_presentations[:10],
        'quickbooks_pending_invoices': pending_invoices[:10],
        'quickbooks_pending_notes': pending_notes[:10],
        'quickbooks_recent_supplier_purchases': recent_supplier_purchases[:5],
        'quickbooks_purchase_presentations': Presentacion.objects.select_related('producto').order_by('producto__nombre', 'nombre'),
        'quickbooks_supplier_purchase_slots': range(4),
        'quickbooks_pending_customer_count': pending_customers.count(),
        'quickbooks_supplier_count': recent_suppliers.count(),
        'quickbooks_pending_presentation_count': pending_presentations.count(),
        'quickbooks_pending_invoice_count': pending_invoices.count(),
        'quickbooks_pending_note_count': pending_notes.count(),
        'quickbooks_pending_supplier_purchase_count': recent_supplier_purchases.filter(quickbooks_id__isnull=True).count(),
        'quickbooks_import_conflicts_count': QuickBooksImportConflict.objects.filter(status=QuickBooksImportConflict.STATUS_CONFLICT).count(),
        'quickbooks_dashboard_feedback': feedback,
    }


def _parse_supplier_purchase_lines(request):
    raw_presentacion_ids = request.POST.getlist('presentacion_id')
    raw_quantities = request.POST.getlist('cantidad')
    raw_unit_costs = request.POST.getlist('costo_unitario')
    raw_descriptions = request.POST.getlist('descripcion')

    line_specs = []
    for index, raw_presentacion_id in enumerate(raw_presentacion_ids):
        presentacion_id = str(raw_presentacion_id or '').strip()
        quantity = str(raw_quantities[index] if index < len(raw_quantities) else '').strip()
        unit_cost = str(raw_unit_costs[index] if index < len(raw_unit_costs) else '').strip()
        description = str(raw_descriptions[index] if index < len(raw_descriptions) else '').strip()
        if not any((presentacion_id, quantity, unit_cost, description)):
            continue
        if not (presentacion_id and quantity and unit_cost):
            raise ValueError('Each supplier purchase line needs product, quantity, and unit cost.')
        if not presentacion_id.isdigit():
            raise ValueError(f'Invalid presentation ID: {presentacion_id}')
        try:
            parsed_quantity = int(quantity)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Invalid quantity value: {quantity}') from exc
        if parsed_quantity <= 0:
            raise ValueError('Quantity must be greater than zero.')
        line_specs.append({
            'presentacion_id': int(presentacion_id),
            'cantidad': parsed_quantity,
            'costo_unitario': unit_cost,
            'descripcion': description,
        })

    if not line_specs:
        raise ValueError('Add at least one supplier purchase line before sending it to QuickBooks.')

    presentation_map = {
        presentacion.id: presentacion
        for presentacion in Presentacion.objects.select_related('producto').filter(id__in=[spec['presentacion_id'] for spec in line_specs])
    }
    if len(presentation_map) != len({spec['presentacion_id'] for spec in line_specs}):
        raise ValueError('One or more selected presentations no longer exist.')

    for spec in line_specs:
        spec['presentacion'] = presentation_map[spec['presentacion_id']]
    return line_specs


@transaction.atomic
def _create_supplier_purchase_from_request(request):
    supplier_name = str(request.POST.get('proveedor_nombre') or '').strip()
    if not supplier_name:
        raise ValueError('Supplier name is required.')

    purchase_date = parse_date(str(request.POST.get('fecha_compra') or '').strip())
    if purchase_date is None:
        raise ValueError('Supplier purchase date is required.')

    raw_due_date = str(request.POST.get('fecha_vencimiento') or '').strip()
    due_date = parse_date(raw_due_date) if raw_due_date else None
    if raw_due_date and due_date is None:
        raise ValueError('Due date is invalid.')

    compra = CompraProveedor.objects.create(
        proveedor_nombre=supplier_name,
        proveedor_email=str(request.POST.get('proveedor_email') or '').strip(),
        proveedor_telefono=str(request.POST.get('proveedor_telefono') or '').strip(),
        bill_number=str(request.POST.get('bill_number') or '').strip(),
        fecha_compra=purchase_date,
        fecha_vencimiento=due_date,
        notas=str(request.POST.get('notas') or '').strip(),
        estado=CompraProveedor.STATUS_DRAFT,
        creado_por=request.user,
    )
    for spec in _parse_supplier_purchase_lines(request):
        line = CompraProveedorLinea(
            compra=compra,
            presentacion=spec['presentacion'],
            cantidad=spec['cantidad'],
            costo_unitario=spec['costo_unitario'],
            descripcion=spec['descripcion'],
        )
        line.full_clean()
        line.save()
    compra.recalcular_totales(save=True)
    return compra


def _quickbooks_center_preview_limit(request):
    raw_limit = str(request.GET.get('preview_limit') or '8').strip()
    if not raw_limit.isdigit():
        return 8
    return max(1, min(int(raw_limit), 20))


def _parse_quickbooks_import_limit(raw_value, *, default=None):
    value = str(raw_value or '').strip()
    if not value:
        return default
    parsed_value = int(value)
    return parsed_value if parsed_value > 0 else None


def _build_quickbooks_preview_context(*, request):
    preview_type = str(request.GET.get('preview') or '').strip().lower()
    if preview_type and _quickbooks_catalog_only_enabled() and preview_type != 'items':
        return {
            'quickbooks_preview_type': preview_type,
            'quickbooks_preview_title': _('Preview disabled'),
            'quickbooks_preview_help': '',
            'quickbooks_preview_columns': [],
            'quickbooks_preview_rows': [],
            'quickbooks_preview_limit': _quickbooks_center_preview_limit(request),
            'quickbooks_preview_error': CATALOG_ONLY_BLOCKED_MESSAGE,
        }
    if not preview_type:
        return {
            'quickbooks_preview_type': '',
            'quickbooks_preview_title': '',
            'quickbooks_preview_help': '',
            'quickbooks_preview_columns': [],
            'quickbooks_preview_rows': [],
            'quickbooks_preview_limit': _quickbooks_center_preview_limit(request),
            'quickbooks_preview_error': '',
        }

    limit = _quickbooks_center_preview_limit(request)
    fetchers = {
        'customers': lambda: fetch_quickbooks_customers(max_results=limit),
        'vendors': lambda: fetch_quickbooks_vendors(max_results=limit),
        'items': lambda: fetch_quickbooks_items(max_results=limit),
        'invoices': lambda: fetch_quickbooks_invoices(max_results=limit),
        'credit_memos': lambda: fetch_quickbooks_credit_memos(max_results=limit),
        'bills': lambda: fetch_quickbooks_bills(max_results=limit),
        'purchase_orders': lambda: fetch_quickbooks_purchase_orders(max_results=limit),
    }
    labels = {
        'customers': (_('Customer preview'), _('Review the customer list before importing it into your system.')),
        'vendors': (_('Supplier preview'), _('Review the supplier list from QuickBooks before importing it into your system.')),
        'items': (_('Catalog preview'), _('Review products and presentations coming from QuickBooks before importing them.')),
        'invoices': (_('Invoice preview'), _('Review sales documents coming from QuickBooks before matching them locally.')),
        'credit_memos': (_('Credit memo preview'), _('Review credit documents before importing or resolving them.')),
        'bills': (_('Bill preview'), _('Review supplier Bills before importing them into local purchases and inventory.')),
        'purchase_orders': (_('Purchase order preview'), _('Review QuickBooks purchase orders before importing them into your local Pre-PO / PO center.')),
    }
    column_map = {
        'customers': [_('Name'), _('Email'), _('Phone'), _('QuickBooks ID')],
        'vendors': [_('Name'), _('Email'), _('Phone'), _('QuickBooks ID')],
        'items': [_('Item'), _('SKU'), _('Price'), _('QuickBooks ID')],
        'invoices': [_('Document'), _('Customer'), _('Total'), _('QuickBooks ID')],
        'credit_memos': [_('Document'), _('Customer'), _('Total'), _('QuickBooks ID')],
        'bills': [_('Document'), _('Vendor'), _('Total'), _('QuickBooks ID')],
        'purchase_orders': [_('Document'), _('Vendor'), _('Total'), _('QuickBooks ID')],
    }

    if preview_type not in fetchers:
        return {
            'quickbooks_preview_type': preview_type,
            'quickbooks_preview_title': _('Preview not available'),
            'quickbooks_preview_help': '',
            'quickbooks_preview_columns': [],
            'quickbooks_preview_rows': [],
            'quickbooks_preview_limit': limit,
            'quickbooks_preview_error': _('The selected preview is not available.'),
        }

    try:
        records = fetchers[preview_type]()
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks hub preview failed for %s: %s', preview_type, exc)
        return {
            'quickbooks_preview_type': preview_type,
            'quickbooks_preview_title': labels[preview_type][0],
            'quickbooks_preview_help': labels[preview_type][1],
            'quickbooks_preview_columns': column_map[preview_type],
            'quickbooks_preview_rows': [],
            'quickbooks_preview_limit': limit,
            'quickbooks_preview_error': str(exc),
        }

    rows = []
    for record in records:
        if preview_type == 'customers':
            rows.append([
                record.get('DisplayName') or record.get('FullyQualifiedName') or '-',
                record.get('PrimaryEmailAddr', {}).get('Address') or '-',
                record.get('PrimaryPhone', {}).get('FreeFormNumber') or '-',
                record.get('Id') or '-',
            ])
        elif preview_type == 'vendors':
            rows.append([
                record.get('DisplayName') or record.get('PrintOnCheckName') or '-',
                record.get('PrimaryEmailAddr', {}).get('Address') or '-',
                record.get('PrimaryPhone', {}).get('FreeFormNumber') or '-',
                record.get('Id') or '-',
            ])
        elif preview_type == 'items':
            rows.append([
                record.get('Name') or '-',
                record.get('Sku') or '-',
                record.get('UnitPrice') or '-',
                record.get('Id') or '-',
            ])
        elif preview_type in {'bills', 'purchase_orders'}:
            rows.append([
                record.get('DocNumber') or '-',
                record.get('VendorRef', {}).get('name') or '-',
                record.get('TotalAmt') or '-',
                record.get('Id') or '-',
            ])
        else:
            rows.append([
                record.get('DocNumber') or '-',
                record.get('CustomerRef', {}).get('name') or '-',
                record.get('TotalAmt') or '-',
                record.get('Id') or '-',
            ])

    return {
        'quickbooks_preview_type': preview_type,
        'quickbooks_preview_title': labels[preview_type][0],
        'quickbooks_preview_help': labels[preview_type][1],
        'quickbooks_preview_columns': column_map[preview_type],
        'quickbooks_preview_rows': rows,
        'quickbooks_preview_limit': limit,
        'quickbooks_preview_error': '',
    }


def _build_quickbooks_center_context(*, request):
    dashboard_context = get_dashboard_sync_context(request=request)
    preview_context = _build_quickbooks_preview_context(request=request)
    conflicts = QuickBooksImportConflict.objects.all()
    connection = get_connection()
    raw_cursors = connection.sync_state.get('cursors', {}) if isinstance(connection.sync_state, dict) else {}
    sync_cursors = []
    for key, label in (
        ('customer', _('Customers')),
        ('vendor', _('Suppliers')),
        ('item', _('Catalog')),
        ('invoice', _('Invoices')),
        ('credit_memo', _('Credit memos')),
        ('bill', _('Bills')),
    ):
        if raw_cursors.get(key):
            sync_cursors.append({'label': label, 'value': raw_cursors[key]})

    return {
        'quickbooks_catalog_only_mode': _quickbooks_catalog_only_enabled(),
        'quickbooks_status': get_connection_status(),
        'quickbooks_recent_conflicts': conflicts[:5],
        'quickbooks_active_conflicts_count': conflicts.filter(status=QuickBooksImportConflict.STATUS_CONFLICT).count(),
        'quickbooks_resolved_conflicts_count': conflicts.exclude(status=QuickBooksImportConflict.STATUS_CONFLICT).count(),
        'quickbooks_sync_cursors': sync_cursors,
        'quickbooks_has_saved_cursors': bool(sync_cursors),
        **preview_context,
        **dashboard_context,
    }


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_center(request):
    return render(request, 'backoffice/quickbooks_hub.html', _build_quickbooks_center_context(request=request))


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def database_backups_center(request):
    return render(request, 'backoffice/database_backups.html', _build_database_backups_context(request=request))


def _validate_restore_confirmation(request):
    confirmation = str(request.POST.get('confirmation') or '').strip().upper()
    replace_current_data = request.POST.get('replace_current_data') == 'yes'
    if not replace_current_data:
        messages.error(request, _('Confirm that the current data will be replaced before restoring.'))
        return False
    if confirmation != 'RESTORE':
        messages.error(request, _('Type RESTORE exactly to confirm the restore operation.'))
        return False
    return True


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def create_database_backup_stored(request):
    try:
        saved_path, backup_name = create_database_backup_file(label='manual')
    except Exception as exc:
        logger.exception('Manual database backup failed: %s', exc)
        messages.error(request, _('Database backup could not be created.'))
        return redirect('database_backups_center')
    messages.success(
        request,
        _('Database backup created and saved on the server: %(name)s') % {'name': backup_name},
    )
    return redirect('database_backups_center')


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def create_system_backup_stored(request):
    try:
        saved_path, backup_name = create_system_backup_file(label='manual')
    except Exception as exc:
        logger.exception('Manual system backup failed: %s', exc)
        messages.error(request, _('System backup could not be created.'))
        return redirect('database_backups_center')
    messages.success(
        request,
        _('System backup created and saved on the server: %(name)s') % {'name': backup_name},
    )
    return redirect('database_backups_center')


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def restore_backup_upload(request):
    uploaded_file = request.FILES.get('backup_file')
    if uploaded_file is None or not str(uploaded_file.name or '').strip():
        messages.error(request, _('Select a backup file to upload before restoring.'))
        return redirect('database_backups_center')
    if not _validate_restore_confirmation(request):
        return redirect('database_backups_center')

    try:
        restored_name = restore_database_backup_upload(uploaded_file=uploaded_file, flush=True)
    except DatabaseBackupError as exc:
        logger.warning('Backup restore from upload failed: %s', exc)
        messages.error(request, str(exc))
        return redirect('database_backups_center')

    return _render_restore_completion(restored_name)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def update_backup_schedule_preference(request):
    selected_schedule = _normalize_backup_schedule(request.POST.get('backup_schedule'))
    _set_backup_schedule_preference(selected_schedule)
    messages.success(request, f'Backup download preference updated to {_backup_schedule_label(selected_schedule)}.')
    return redirect('database_backups_center')


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_login(request):
    if not quickbooks_credentials_configured():
        messages.error(request, quickbooks_credentials_setup_message())
        redirect_to = _resolve_dashboard_redirect(request) or reverse('quickbooks_center')
        return redirect(redirect_to)
    try:
        oauth_url = get_oauth_login_url(request=request)
    except (QuickBooksServiceError, QuickBooksConfigurationError) as exc:
        messages.error(request, str(exc))
        return redirect(get_redirect_url_for_user(request.user))
    return redirect(oauth_url)


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_callback(request):
    try:
        connection = handle_oauth_callback(
            request=request,
            code=request.GET.get('code', ''),
            state=request.GET.get('state', ''),
            realm_id=request.GET.get('realmId', ''),
        )
    except QuickBooksServiceError as exc:
        logger.warning('QuickBooks callback failed: %s', exc)
        messages.error(request, str(exc))
        return redirect(get_redirect_url_for_user(request.user))
    messages.success(request, f'QuickBooks connected successfully. Realm ID: {connection.realm_id}')
    return redirect(get_redirect_url_for_user(request.user))


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_status(request):
    status = get_connection_status()
    if _resolve_dashboard_redirect(request):
        return _response_or_redirect(request, operation='status', result={'connected': status.get('is_active'), 'status': status})
    return JsonResponse(status)


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_test_connection(request):
    if not quickbooks_credentials_configured():
        setup_message = quickbooks_credentials_setup_message()
        if _resolve_dashboard_redirect(request):
            return _response_or_redirect(request, operation='test_connection', error=setup_message, status_code=503)
        return JsonResponse(_status_payload(connection_status=get_connection_status(), error=setup_message), status=503)

    connection = get_connection()
    if not connection.is_active:
        if _resolve_dashboard_redirect(request):
            return _response_or_redirect(request, operation='test_connection', error='QuickBooks is not connected yet.', status_code=503)
        return JsonResponse(_status_payload(connection_status=get_connection_status(), error='QuickBooks is not connected yet.'), status=503)
    try:
        company = QuickBooksAPIClient(connection=connection).get_company_info()
    except QuickBooksConfigurationError as exc:
        logger.warning('QuickBooks test connection blocked: %s', exc)
        if _resolve_dashboard_redirect(request):
            return _response_or_redirect(request, operation='test_connection', error=str(exc), status_code=503)
        return JsonResponse(_status_payload(connection_status=get_connection_status(), error=str(exc)), status=503)
    except (QuickBooksServiceError, QuickBooksAPIError) as exc:
        logger.warning('QuickBooks test connection failed: %s', exc)
        if _resolve_dashboard_redirect(request):
            return _response_or_redirect(request, operation='test_connection', error=str(exc), status_code=502)
        return JsonResponse(_status_payload(connection_status=get_connection_status(), error=str(exc)), status=502)
    result = _status_payload(connection_status=get_connection_status(), connected=True, company=company)
    if _resolve_dashboard_redirect(request):
        return _response_or_redirect(request, operation='test_connection', result=result)
    return JsonResponse(result)


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_customers(request):
    try:
        result = fetch_quickbooks_customers(max_results=_parse_quickbooks_import_limit(request.GET.get('limit'), default=None))
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks customer import preview failed: %s', exc)
        return _response_or_redirect(request, operation='import_customers', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_customers', result={'count': len(result), 'customers': result})


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_vendors(request):
    try:
        result = fetch_quickbooks_vendors(max_results=request.GET.get('limit', 25))
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks vendor import preview failed: %s', exc)
        return _response_or_redirect(request, operation='import_vendors', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_vendors', result={'count': len(result), 'vendors': result})


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_items(request):
    try:
        result = fetch_quickbooks_items(max_results=request.GET.get('limit', 25))
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks item import preview failed: %s', exc)
        return _response_or_redirect(request, operation='import_items', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_items', result={'count': len(result), 'items': result})


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_bills(request):
    try:
        result = fetch_quickbooks_bills(max_results=request.GET.get('limit', 25))
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks Bill import preview failed: %s', exc)
        return _response_or_redirect(request, operation='import_bills', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_bills', result={'count': len(result), 'bills': result})


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_purchase_orders(request):
    try:
        result = fetch_quickbooks_purchase_orders(max_results=request.GET.get('limit', 25))
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks purchase order import preview failed: %s', exc)
        return _response_or_redirect(request, operation='import_purchase_orders', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_purchase_orders', result={'count': len(result), 'purchase_orders': result})


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_customers_to_local(request):
    try:
        result = import_quickbooks_customers(max_results=_parse_quickbooks_import_limit(request.POST.get('limit'), default=None))
    except (ValueError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks customer import to local failed: %s', exc)
        return _response_or_redirect(request, operation='import_customers_to_local', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_customers_to_local', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_vendors_to_local(request):
    try:
        result = import_quickbooks_vendors(max_results=int(request.POST.get('limit', 25) or 25))
    except (ValueError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks vendor import to local failed: %s', exc)
        return _response_or_redirect(request, operation='import_vendors_to_local', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_vendors_to_local', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_import_items_to_local(request):
    try:
        result = import_quickbooks_items(max_results=int(request.POST.get('limit', 25) or 25))
    except (ValueError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks item import to local failed: %s', exc)
        return _response_or_redirect(request, operation='import_items_to_local', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_items_to_local', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_bills_to_local(request):
    try:
        result = import_quickbooks_bills(max_results=int(request.POST.get('limit', 25) or 25))
    except (ValueError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks Bill import to local failed: %s', exc)
        return _response_or_redirect(request, operation='import_bills_to_local', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_bills_to_local', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_purchase_orders_to_local(request):
    try:
        result = import_quickbooks_purchase_orders(max_results=int(request.POST.get('limit', 25) or 25))
    except (ValueError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks purchase order import to local failed: %s', exc)
        return _response_or_redirect(request, operation='import_purchase_orders_to_local', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_purchase_orders_to_local', result=result)


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_invoices(request):
    try:
        result = fetch_quickbooks_invoices(max_results=request.GET.get('limit', 25))
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks invoice import preview failed: %s', exc)
        return _response_or_redirect(request, operation='import_invoices', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_invoices', result={'count': len(result), 'invoices': result})


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_credit_memos(request):
    try:
        result = fetch_quickbooks_credit_memos(max_results=request.GET.get('limit', 25))
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks credit memo import preview failed: %s', exc)
        return _response_or_redirect(request, operation='import_credit_memos', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_credit_memos', result={'count': len(result), 'credit_memos': result})


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_accounting_documents_to_local(request):
    try:
        result = import_quickbooks_accounting_documents(
            max_results=_parse_quickbooks_import_limit(request.POST.get('limit'), default=None)
        )
    except (ValueError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks accounting import to local failed: %s', exc)
        return _response_or_redirect(request, operation='import_accounting_documents_to_local', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='import_accounting_documents_to_local', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_start_task(request):
    operation = str(request.POST.get('operation') or '').strip()
    if _quickbooks_catalog_only_enabled() and operation != 'import_items_to_local':
        blocked = _guard_quickbooks_catalog_only(request, operation='task_start')
        if blocked is not None:
            return blocked
    limit_raw = request.POST.get('limit')
    try:
        limit = _parse_quickbooks_import_limit(limit_raw, default=None)
    except Exception:
        limit = None

    # map allowed operations to internal functions
    op_map = {
        'import_customers_to_local': import_quickbooks_customers,
        'import_vendors_to_local': import_quickbooks_vendors,
        'import_items_to_local': import_quickbooks_items,
        'import_bills_to_local': import_quickbooks_bills,
        'import_purchase_orders_to_local': import_quickbooks_purchase_orders,
        'import_accounting_documents_to_local': import_quickbooks_accounting_documents,
        'pull_sync_to_local': pull_quickbooks_to_local,
    }

    func = op_map.get(operation)
    if not func:
        return _response_or_redirect(request, operation='task_start', error='Unsupported operation', status_code=400)

    task_id = uuid.uuid4().hex
    cache_key = f'quickbooks_task_{task_id}'
    cache.set(cache_key, {'status': 'running', 'progress': 0, 'operation': operation}, timeout=60 * 60)

    def _runner(task_key, fn, limit_value):
        try:
            cache.set(task_key, {'status': 'running', 'progress': 5, 'operation': operation}, timeout=60 * 60)
            # run the operation
            # prefer passing the task cache key to the operation so it can report progress
            try:
                result = fn(max_results=limit_value, task_cache_key=task_key)
            except TypeError:
                # fallback for functions that don't accept task_cache_key
                result = fn(max_results=limit_value) if 'max_results' in fn.__code__.co_varnames or True else fn()
            # mark completed
            cache.set(task_key, {'status': 'completed', 'progress': 100, 'operation': operation, 'result': result}, timeout=60 * 60)
        except Exception as exc:
            cache.set(task_key, {'status': 'failed', 'progress': 100, 'operation': operation, 'error': str(exc)}, timeout=60 * 60)

    thread = threading.Thread(target=_runner, args=(cache_key, func, limit), daemon=True)
    thread.start()

    return JsonResponse({'task_id': task_id})


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_task_status(request, task_id):
    cache_key = f'quickbooks_task_{task_id}'
    data = cache.get(cache_key)
    if not data:
        return _response_or_redirect(request, operation='task_status', error='Task not found', status_code=404)
    return JsonResponse({'status': data.get('status'), 'progress': int(data.get('progress') or 0), 'operation': data.get('operation'), 'result': data.get('result', None), 'error': data.get('error', None)})


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_pull_sync_to_local(request):
    try:
        result = pull_quickbooks_to_local(
            max_results=int(request.POST.get('limit', 25) or 25),
            force_full=request.POST.get('mode') == 'full',
        )
    except (ValueError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks pull sync failed: %s', exc)
        return _response_or_redirect(request, operation='pull_sync_to_local', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='pull_sync_to_local', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_database_backup(request):
    backup_schedule = _normalize_backup_schedule(request.POST.get('backup_schedule') or _get_backup_schedule_preference())
    try:
        saved_path, backup_name = create_database_backup_file(label=backup_schedule)
    except Exception as exc:
        logger.exception('Database backup generation failed: %s', exc)
        return _response_or_redirect(request, operation='database_backup', error='Database backup could not be created.', status_code=500)

    backup_file = _get_backup_storage().open(saved_path, 'rb')
    response = FileResponse(backup_file, as_attachment=True, filename=backup_name, content_type='application/gzip')
    response['X-Backup-Path'] = str(Path(settings.MEDIA_ROOT) / saved_path)
    response['X-Backup-Schedule'] = backup_schedule
    return response


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def system_backup(request):
    backup_schedule = _normalize_backup_schedule(request.POST.get('backup_schedule') or _get_backup_schedule_preference())
    try:
        saved_path, backup_name = create_system_backup_file(label=backup_schedule)
    except Exception as exc:
        logger.exception('System backup generation failed: %s', exc)
        return _response_or_redirect(request, operation='system_backup', error='System backup could not be created.', status_code=500)

    backup_file = _get_backup_storage().open(saved_path, 'rb')
    response = FileResponse(backup_file, as_attachment=True, filename=backup_name, content_type='application/gzip')
    response['X-Backup-Path'] = str(Path(settings.MEDIA_ROOT) / saved_path)
    response['X-Backup-Schedule'] = backup_schedule
    return response


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_database_backup_download(request, backup_name):
    try:
        backup_file, saved_path, normalized_name = open_database_backup(backup_name)
    except DatabaseBackupError as exc:
        messages.error(request, str(exc))
        return redirect('database_backups_center')

    response = FileResponse(backup_file, as_attachment=True, filename=normalized_name, content_type='application/gzip')
    response['X-Backup-Path'] = str(Path(settings.MEDIA_ROOT) / saved_path)
    return response


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def system_backup_download(request, backup_name):
    try:
        backup_file, saved_path, normalized_name = open_system_backup(backup_name)
    except DatabaseBackupError as exc:
        messages.error(request, str(exc))
        return redirect('database_backups_center')

    response = FileResponse(backup_file, as_attachment=True, filename=normalized_name, content_type='application/gzip')
    response['X-Backup-Path'] = str(Path(settings.MEDIA_ROOT) / saved_path)
    return response


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def restore_backup_from_center(request):
    backup_name = str(request.POST.get('backup_name') or '').strip()

    if not backup_name:
        messages.error(request, _('Select a backup file before restoring.'))
        return redirect('database_backups_center')
    if not _validate_restore_confirmation(request):
        return redirect('database_backups_center')

    try:
        restored_name = restore_database_backup_file(source=backup_name, flush=True)
    except DatabaseBackupError as exc:
        logger.warning('Backup restore from center failed: %s', exc)
        messages.error(request, str(exc))
        return redirect('database_backups_center')

    return _render_restore_completion(restored_name)


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_import_conflicts(request):
    conflicts = QuickBooksImportConflict.objects.all()
    return render(
        request,
        'backoffice/quickbooks_import_conflicts.html',
        {
            'conflicts': conflicts,
            'active_conflicts_count': conflicts.filter(status=QuickBooksImportConflict.STATUS_CONFLICT).count(),
            'quickbooks_status': get_connection_status(),
        },
    )


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_import_conflict_retry(request, conflict_id):
    try:
        conflict = QuickBooksImportConflict.objects.get(pk=conflict_id)
        if _quickbooks_catalog_only_enabled() and conflict.entity_type != QuickBooksImportConflict.ENTITY_ITEM:
            messages.error(request, CATALOG_ONLY_BLOCKED_MESSAGE)
            return redirect(_conflicts_redirect_target(request))
        retry_quickbooks_import_conflict(conflict, user=request.user)
    except QuickBooksImportConflict.DoesNotExist:
        messages.error(request, 'QuickBooks conflict not found.')
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError, ObjectDoesNotExist) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'QuickBooks conflict retried successfully.')
    return redirect(_conflicts_redirect_target(request))


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_import_conflict_link(request, conflict_id):
    try:
        conflict = QuickBooksImportConflict.objects.get(pk=conflict_id)
        link_quickbooks_import_conflict(
            conflict,
            local_record_id=request.POST.get('local_record_id'),
            local_model=request.POST.get('local_model'),
            user=request.user,
            resolution_note=(request.POST.get('resolution_note') or '').strip(),
        )
    except QuickBooksImportConflict.DoesNotExist:
        messages.error(request, 'QuickBooks conflict not found.')
    except (ValueError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError, ObjectDoesNotExist) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'QuickBooks conflict linked successfully.')
    return redirect(_conflicts_redirect_target(request))


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
def quickbooks_import_conflict_dismiss(request, conflict_id):
    try:
        conflict = QuickBooksImportConflict.objects.get(pk=conflict_id)
        dismiss_quickbooks_import_conflict(
            conflict,
            user=request.user,
            resolution_note=(request.POST.get('resolution_note') or '').strip(),
        )
    except QuickBooksImportConflict.DoesNotExist:
        messages.error(request, 'QuickBooks conflict not found.')
    else:
        messages.success(request, 'QuickBooks conflict dismissed.')
    return redirect(_conflicts_redirect_target(request))


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_customer(request, cliente_id):
    try:
        result = sync_customer_by_id(cliente_id)
    except ObjectDoesNotExist:
        return _response_or_redirect(request, operation='sync_customer', error='Customer not found.', status_code=404)
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks customer sync failed: %s', exc)
        return _response_or_redirect(request, operation='sync_customer', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='sync_customer', result=result)


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_product(request, presentacion_id):
    try:
        result = sync_product_by_id(presentacion_id)
    except ObjectDoesNotExist:
        return _response_or_redirect(request, operation='sync_product', error='Product presentation not found.', status_code=404)
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks product sync failed: %s', exc)
        return _response_or_redirect(request, operation='sync_product', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='sync_product', result=result)


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_invoice(request, invoice_id):
    try:
        result = sync_invoice_by_id(invoice_id)
    except ObjectDoesNotExist:
        return _response_or_redirect(request, operation='sync_invoice', error='Invoice not found.', status_code=404)
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks invoice sync failed: %s', exc)
        return _response_or_redirect(request, operation='sync_invoice', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='sync_invoice', result=result)


@require_GET
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_adjustment_note(request, note_id):
    try:
        result = sync_adjustment_note_by_id(note_id)
    except ObjectDoesNotExist:
        return _response_or_redirect(request, operation='sync_adjustment_note', error='Adjustment note not found.', status_code=404)
    except (QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks adjustment note sync failed: %s', exc)
        return _response_or_redirect(request, operation='sync_adjustment_note', error=str(exc), status_code=502)
    return _response_or_redirect(request, operation='sync_adjustment_note', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_supplier_purchase_create(request):
    compra = None
    try:
        purchase_id = str(request.POST.get('purchase_id') or '').strip()
        if purchase_id:
            compra = CompraProveedor.objects.prefetch_related('lineas__presentacion__producto').get(pk=purchase_id)
        else:
            compra = _create_supplier_purchase_from_request(request)
        result = sync_supplier_purchase(compra=compra)
        if compra.estado == CompraProveedor.STATUS_DRAFT:
            compra.estado = CompraProveedor.STATUS_SENT
            compra.save(update_fields=['estado', 'actualizado_en'])
    except (ObjectDoesNotExist, ValueError, ValidationError, QuickBooksServiceError, QuickBooksAPIError, QuickBooksSyncError) as exc:
        logger.warning('QuickBooks supplier purchase sync failed: %s', exc)
        return _response_or_redirect(request, operation='sync_supplier_purchase', error=str(exc), status_code=502)

    payload = dict(result)
    payload['purchase_id'] = compra.id
    payload['supplier_name'] = compra.proveedor_nombre
    return _response_or_redirect(request, operation='sync_supplier_purchase', result=payload)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_customers_batch(request):
    try:
        result = sync_customer_batch_by_ids(_parse_id_list(request.POST.get('ids', '')))
    except ValueError as exc:
        return _response_or_redirect(request, operation='sync_customers_batch', error=str(exc), status_code=400)
    return _response_or_redirect(request, operation='sync_customers_batch', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_products_batch(request):
    try:
        result = sync_product_batch_by_ids(_parse_id_list(request.POST.get('ids', '')))
    except ValueError as exc:
        return _response_or_redirect(request, operation='sync_products_batch', error=str(exc), status_code=400)
    return _response_or_redirect(request, operation='sync_products_batch', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_invoices_batch(request):
    try:
        result = sync_invoice_batch_by_ids(_parse_id_list(request.POST.get('ids', '')))
    except ValueError as exc:
        return _response_or_redirect(request, operation='sync_invoices_batch', error=str(exc), status_code=400)
    return _response_or_redirect(request, operation='sync_invoices_batch', result=result)


@require_POST
@internal_permission_required('admin.dashboard.view', 'backoffice.dashboard.view')
@quickbooks_requires_full_mode
def quickbooks_sync_adjustment_notes_batch(request):
    try:
        result = sync_adjustment_note_batch_by_ids(_parse_id_list(request.POST.get('ids', '')))
    except ValueError as exc:
        return _response_or_redirect(request, operation='sync_adjustment_notes_batch', error=str(exc), status_code=400)
    return _response_or_redirect(request, operation='sync_adjustment_notes_batch', result=result)