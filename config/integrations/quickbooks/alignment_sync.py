import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from config.clientes.models import Cliente
from config.integrations.models import QuickBooksSyncRun
from config.integrations.quickbooks.services import get_connection
from config.productos.models import Presentacion

from .sync import (
    pull_quickbooks_to_local,
    refresh_linked_quickbooks_invoice_status,
    sync_customer_batch_by_ids,
    sync_product_batch_by_ids,
)

logger = logging.getLogger(__name__)

ALIGNMENT_TIMEZONE_NAME = 'America/New_York'
SCHEDULED_ALIGNMENT_HOURS = frozenset({0, 6, 12, 18})
SCHEDULED_ALIGNMENT_HOUR_LABELS = (
    (0, '12 AM'),
    (6, '6 AM'),
    (12, '12 PM'),
    (18, '6 PM'),
)


def alignment_schedule_label():
    return ', '.join(label for _hour, label in SCHEDULED_ALIGNMENT_HOUR_LABELS)


def alignment_timezone():
    return ZoneInfo(getattr(settings, 'QUICKBOOKS_ALIGNMENT_TIMEZONE', ALIGNMENT_TIMEZONE_NAME))


def _empty_batch_result():
    return {
        'requested_ids': [],
        'success_count': 0,
        'failed_count': 0,
        'results': [],
    }


def _pending_new_customer_ids():
    return list(
        Cliente.objects.filter(Q(quickbooks_id__isnull=True) | Q(quickbooks_id=''))
        .order_by('id')
        .values_list('id', flat=True)
    )


def _pending_new_presentation_ids():
    return list(
        Presentacion.objects.filter(Q(quickbooks_id__isnull=True) | Q(quickbooks_id=''))
        .order_by('id')
        .values_list('id', flat=True)
    )


def push_new_outbound_records_to_quickbooks(*, max_records=None):
    """Export only local customers and products that were never sent to QuickBooks."""
    customer_ids = _pending_new_customer_ids()
    presentation_ids = _pending_new_presentation_ids()
    if max_records is not None:
        customer_ids = customer_ids[:max_records]
        presentation_ids = presentation_ids[:max_records]

    customers_result = sync_customer_batch_by_ids(customer_ids) if customer_ids else _empty_batch_result()
    presentations_result = sync_product_batch_by_ids(presentation_ids) if presentation_ids else _empty_batch_result()
    return {
        'customers': customers_result,
        'presentations': presentations_result,
        'invoices_skipped': True,
        'notes_skipped': True,
        'pending_customer_count': len(customer_ids),
        'pending_presentation_count': len(presentation_ids),
    }


def _entity_import_counts(section):
    return {
        'created': int(section.get('created_count') or 0),
        'updated': int(section.get('updated_count') or 0),
        'conflicts': int(section.get('conflict_count') or 0),
        'skipped': int(section.get('skipped_count') or 0),
        'failed': int(section.get('failed_count') or 0),
    }


def _export_samples(batch_result, *, limit=8):
    samples = []
    for item in batch_result.get('results') or []:
        if not item.get('ok'):
            samples.append({
                'id': item.get('id'),
                'ok': False,
                'error': item.get('error') or _('Unknown error'),
            })
            continue
        payload = item.get('result') or {}
        samples.append({
            'id': item.get('id'),
            'ok': True,
            'action': payload.get('action') or 'synced',
            'quickbooks_id': payload.get('quickbooks_id') or (payload.get('payload') or {}).get('Id'),
        })
        if len(samples) >= limit:
            break
    return samples


def build_alignment_sync_summary(*, pull_result, invoice_status_result, export_result, force_full=False):
    customers = pull_result.get('customers') or {}
    items = pull_result.get('items') or {}
    export_customers = export_result.get('customers') or {}
    export_presentations = export_result.get('presentations') or {}
    return {
        'incremental': not force_full,
        'force_full': force_full,
        'import': {
            'customers': _entity_import_counts(customers),
            'items': _entity_import_counts(items),
            'invoice_status': {
                'linked': int(invoice_status_result.get('linked_count') or invoice_status_result.get('count') or 0),
                'updated': int(invoice_status_result.get('updated_count') or 0),
                'skipped': int(invoice_status_result.get('skipped_count') or 0),
                'missing': int(invoice_status_result.get('missing_count') or 0),
            },
        },
        'export': {
            'customers': {
                'requested': len(export_customers.get('requested_ids') or []),
                'success': int(export_customers.get('success_count') or 0),
                'failed': int(export_customers.get('failed_count') or 0),
                'samples': _export_samples(export_customers),
            },
            'presentations': {
                'requested': len(export_presentations.get('requested_ids') or []),
                'success': int(export_presentations.get('success_count') or 0),
                'failed': int(export_presentations.get('failed_count') or 0),
                'samples': _export_samples(export_presentations),
            },
            'invoices_skipped': True,
            'notes_skipped': True,
        },
    }


def _resolve_alignment_status(*, summary, error_message=''):
    if error_message:
        return QuickBooksSyncRun.STATUS_FAILED
    export_failed = (
        summary.get('export', {}).get('customers', {}).get('failed', 0)
        + summary.get('export', {}).get('presentations', {}).get('failed', 0)
    )
    import_failed = summary.get('import', {}).get('items', {}).get('failed', 0)
    if export_failed or import_failed:
        return QuickBooksSyncRun.STATUS_PARTIAL
    return QuickBooksSyncRun.STATUS_SUCCESS


def _default_skip_images():
    return bool(getattr(settings, 'QUICKBOOKS_CATALOG_SYNC_SKIP_IMAGES', True))


def run_quickbooks_alignment_sync(
    *,
    force_full=False,
    trigger=QuickBooksSyncRun.TRIGGER_MANUAL,
    max_results=None,
    task_cache_key=None,
    skip_images=None,
    save_history=True,
    scheduled_slot='',
):
    skip_images = _default_skip_images() if skip_images is None else bool(skip_images)
    sync_run = None
    if save_history:
        sync_run = QuickBooksSyncRun.objects.create(
            trigger=trigger,
            status=QuickBooksSyncRun.STATUS_RUNNING,
            force_full=force_full,
            scheduled_slot=scheduled_slot,
            timezone_name=str(alignment_timezone()),
        )

    try:
        pull_result = pull_quickbooks_to_local(
            max_results=max_results,
            force_full=force_full,
            task_cache_key=task_cache_key,
            skip_images=skip_images,
        )
        invoice_status_result = refresh_linked_quickbooks_invoice_status(
            max_results=max_results,
            task_cache_key=task_cache_key,
            force_all=force_full,
        )
        export_result = push_new_outbound_records_to_quickbooks()
        summary = build_alignment_sync_summary(
            pull_result=pull_result,
            invoice_status_result=invoice_status_result,
            export_result=export_result,
            force_full=force_full,
        )
        status = _resolve_alignment_status(summary=summary)
        result = {
            'pull': pull_result,
            'invoice_status': invoice_status_result,
            'export': export_result,
            'summary': summary,
            'sync_run_id': sync_run.pk if sync_run else None,
            'force_full': force_full,
            'incremental': not force_full,
        }
        if sync_run is not None:
            sync_run.status = status
            sync_run.summary = summary
            sync_run.finished_at = timezone.now()
            sync_run.save(update_fields=['status', 'summary', 'finished_at'])
        return result
    except Exception as exc:
        logger.exception('QuickBooks alignment sync failed: %s', exc)
        if sync_run is not None:
            sync_run.status = QuickBooksSyncRun.STATUS_FAILED
            sync_run.error_message = str(exc)
            sync_run.finished_at = timezone.now()
            sync_run.save(update_fields=['status', 'error_message', 'finished_at'])
        raise


def current_alignment_slot_key(*, now=None):
    localized = now or datetime.now(alignment_timezone())
    if localized.hour not in SCHEDULED_ALIGNMENT_HOURS:
        return None
    return localized.strftime('%Y-%m-%dT%H:00')


def alignment_slot_is_due(*, now=None, force=False):
    if force:
        return True, current_alignment_slot_key(now=now) or 'forced'
    slot_key = current_alignment_slot_key(now=now)
    if not slot_key:
        return False, ''
    connection = get_connection()
    state = dict(connection.sync_state or {})
    automation = dict(state.get('alignment_automation') or {})
    if automation.get('last_slot') == slot_key:
        return False, slot_key
    return True, slot_key


def mark_alignment_slot_completed(*, slot_key):
    connection = get_connection()
    state = dict(connection.sync_state or {})
    automation = dict(state.get('alignment_automation') or {})
    automation['last_slot'] = slot_key
    automation['last_run_at'] = timezone.now().isoformat()
    state['alignment_automation'] = automation
    connection.sync_state = state
    connection.save(update_fields=['sync_state', 'updated_at'])


def record_skipped_alignment_run(*, slot_key='', reason=''):
    return QuickBooksSyncRun.objects.create(
        trigger=QuickBooksSyncRun.TRIGGER_SCHEDULED,
        status=QuickBooksSyncRun.STATUS_SKIPPED,
        scheduled_slot=slot_key,
        timezone_name=str(alignment_timezone()),
        summary={'skipped_reason': reason},
        finished_at=timezone.now(),
    )
