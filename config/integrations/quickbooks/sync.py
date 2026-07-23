import logging
import re
import time
import unicodedata
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.utils import timezone
from django.core.cache import cache
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext as _

from config.clientes.models import Cliente
from config.facturacion.models import Invoice, InvoiceItem, NotaAjuste
from config.integrations.models import QuickBooksImportConflict
from config.inventario.models import CompraProveedor, CompraProveedorLinea, Proveedor, StockPresentacion
from config.inventario.services import registrar_recepcion_compra_proveedor
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Categoria, Marca, PRESENTACION_TERM_TRANSLATIONS, Presentacion, Producto
from config.usuarios.models import Usuario

from .client import QuickBooksAPIClient, QuickBooksAPIError
from .constants import QUICKBOOKS_SYNC_STATUS_FAILED, QUICKBOOKS_SYNC_STATUS_PENDING, QUICKBOOKS_SYNC_STATUS_SYNCED
from .services import QuickBooksServiceError, ensure_valid_access_token


class QuickBooksSyncError(Exception):
    pass


logger = logging.getLogger(__name__)


SYNC_CURSOR_OVERLAP_SECONDS = 60


def is_sync_locked(instance):
    return bool(getattr(instance, 'quickbooks_id', '') and getattr(instance, 'sync_status', '') == QUICKBOOKS_SYNC_STATUS_SYNCED)


def ensure_record_is_not_locked(instance, *, label='Record'):
    if is_sync_locked(instance):
        identifier = getattr(instance, 'numero', None) or getattr(instance, 'pk', '-')
        raise QuickBooksSyncError(f'{label} {identifier} is locked because it is already synced with QuickBooks.')


def _quantize_money(value):
    return Decimal(str(value or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _as_float(value):
    return float(_quantize_money(value))


def _normalize_text(value, *, fallback=''):
    return (value or fallback or '').strip()


def _looks_like_quickbooks_deleted_label(value):
    normalized = _normalize_text(value).lower()
    if not normalized:
        return False
    return '(deleted)' in normalized or '(eliminado)' in normalized


def _quickbooks_payload_active(payload):
    if not payload:
        return False
    if 'Active' not in payload:
        return True
    value = payload.get('Active')
    if value is None:
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'false', '0', 'no', 'off'}:
            return False
        if normalized in {'true', '1', 'yes', 'on'}:
            return True
    return bool(value)


def _resolve_quickbooks_item_active(payload, *, client=None, fetch_when_missing=False, force_refresh=False):
    if not payload:
        return False
    item_id = str(payload.get('Id') or '').strip()
    if force_refresh and item_id:
        client = client or QuickBooksAPIClient()
        full_payload = _fetch_quickbooks_item_payload(item_id=item_id, client=client)
        if full_payload and 'Active' in full_payload:
            return _quickbooks_payload_active(full_payload)
    if 'Active' in payload:
        return _quickbooks_payload_active(payload)
    if not fetch_when_missing:
        # Bulk catalog queries already scope to Active items; avoid per-item API calls.
        return True
    if not item_id:
        return False
    client = client or QuickBooksAPIClient()
    full_payload = _fetch_quickbooks_item_payload(item_id=item_id, client=client)
    if full_payload and 'Active' in full_payload:
        return _quickbooks_payload_active(full_payload)
    return False


def _find_local_presentacion_for_quickbooks_item(quickbooks_id, *, prefetched_presentacion=None):
    qb_id = str(quickbooks_id or '').strip()
    if not qb_id:
        return None
    presentacion = prefetched_presentacion
    if presentacion is not None:
        linked_ids = {
            str(getattr(presentacion, 'quickbooks_id', '') or '').strip(),
            str(getattr(getattr(presentacion, 'producto', None), 'quickbooks_id', '') or '').strip(),
        }
        if qb_id in linked_ids:
            return presentacion
        presentacion = None
    if presentacion is None:
        presentacion = (
            Presentacion.objects.select_related('producto')
            .filter(Q(quickbooks_id=qb_id) | Q(producto__quickbooks_id=qb_id))
            .first()
        )
    return presentacion


def _linked_catalog_presentacion_queryset():
    return (
        Presentacion.objects.select_related('producto')
        .filter(
            Q(quickbooks_id__isnull=False) & ~Q(quickbooks_id='')
            | Q(producto__quickbooks_id__isnull=False) & ~Q(producto__quickbooks_id='')
        )
        .order_by('producto__nombre', 'id')
    )


def _linked_catalog_quickbooks_ids(presentaciones):
    qb_ids = set()
    for presentacion in presentaciones:
        for raw_id in (presentacion.quickbooks_id, getattr(presentacion.producto, 'quickbooks_id', None)):
            qb_id = str(raw_id or '').strip()
            if qb_id:
                qb_ids.add(qb_id)
    return sorted(qb_ids)


def _quickbooks_record_is_active(payload):
    return _quickbooks_payload_active(payload)


def _quickbooks_ref_looks_deleted(ref):
    ref = ref or {}
    return _looks_like_quickbooks_deleted_label(ref.get('name'))


def _quickbooks_customer_payload_is_importable(payload):
    if not payload or not _quickbooks_record_is_active(payload):
        return False
    for name in (
        payload.get('DisplayName'),
        payload.get('CompanyName'),
        payload.get('PrintOnCheckName'),
        payload.get('FullyQualifiedName'),
    ):
        if _looks_like_quickbooks_deleted_label(name):
            return False
    return True


def _quickbooks_item_payload_is_importable(payload):
    if not payload:
        return False
    for name in (payload.get('Name'), payload.get('FullyQualifiedName')):
        if _looks_like_quickbooks_deleted_label(name):
            return False
    return True


def _quickbooks_accounting_document_is_importable(payload, *, customer_payload=None):
    if not payload:
        return False
    if _quickbooks_ref_looks_deleted(payload.get('CustomerRef')):
        return False
    if customer_payload is not None and not _quickbooks_customer_payload_is_importable(customer_payload):
        return False
    return True


def _skip_import_result(*, entity, quickbooks_id, label='', reason=''):
    return {
        'ok': True,
        'action': 'skipped',
        'entity': entity,
        'quickbooks_id': quickbooks_id,
        'label': label or quickbooks_id,
        'reason': reason,
    }


def _truncate(value, limit=100):
    return _normalize_text(value)[:limit]


def _parse_quickbooks_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(str(value).replace('Z', '+00:00'))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _parse_quickbooks_date(value):
    if not value:
        return None
    return parse_date(str(value).strip())


def _quickbooks_linked_payment_ids(payload):
    linked = payload.get('LinkedTxn') or []
    if isinstance(linked, dict):
        linked = [linked]
    return [
        str(item.get('TxnId') or '').strip()
        for item in linked
        if str(item.get('TxnType') or '').lower() == 'payment' and str(item.get('TxnId') or '').strip()
    ]


def _normalize_quickbooks_email_status(value):
    normalized = _normalize_text(value).upper().replace(' ', '_')
    mapping = {
        'NOTSET': 'NOT_SET',
        'NEEDTOSEND': 'NEED_TO_SEND',
        'EMAILSENT': 'EMAIL_SENT',
    }
    return mapping.get(normalized, normalized or 'NOT_SET')


def _quickbooks_payment_is_deposited(payment_payload):
    if not payment_payload:
        return False
    deposit_ref = payment_payload.get('DepositToAccountRef') or {}
    account_name = _normalize_text(deposit_ref.get('name')).lower()
    if not account_name:
        return False
    return 'undeposited' not in account_name


def _fetch_quickbooks_invoice_deposited_status(payload, *, client=None):
    if _as_float(payload.get('Balance') or 0) > 0:
        return False
    payment_ids = _quickbooks_linked_payment_ids(payload)
    if not payment_ids or client is None:
        return False
    client = client or QuickBooksAPIClient()
    for payment_id in payment_ids:
        try:
            payment_payload = client.read_entity('Payment', payment_id) or client.find_by_id('Payment', payment_id)
        except QuickBooksAPIError:
            continue
        if _quickbooks_payment_is_deposited(payment_payload):
            return True
    return False


def _derive_quickbooks_invoice_status(payload, *, client=None, skip_deposited_lookup=False):
    balance = _as_float(payload.get('Balance') or 0)
    total = _as_float(payload.get('TotalAmt') or balance)
    due_date = _parse_quickbooks_date(payload.get('DueDate'))
    email_status = _normalize_quickbooks_email_status(payload.get('EmailStatus'))

    if total > 0 and balance <= 0:
        if not skip_deposited_lookup and _fetch_quickbooks_invoice_deposited_status(payload, client=client):
            payment_status = 'DEPOSITED'
        else:
            payment_status = 'PAID'
    elif due_date:
        today = timezone.localdate()
        if due_date > today:
            payment_status = 'DUE'
        elif due_date == today:
            payment_status = 'DUE_TODAY'
        else:
            payment_status = 'OVERDUE'
    else:
        payment_status = 'OPEN'

    return payment_status, due_date, email_status


def _apply_quickbooks_invoice_status_to_local_record(invoice, payload, *, client=None):
    payment_status, due_date, email_status = _derive_quickbooks_invoice_status(payload, client=client)
    update_fields = []
    if invoice.qb_payment_status != payment_status:
        invoice.qb_payment_status = payment_status
        update_fields.append('qb_payment_status')
    if invoice.qb_due_date != due_date:
        invoice.qb_due_date = due_date
        update_fields.append('qb_due_date')
    if invoice.qb_email_status != email_status:
        invoice.qb_email_status = email_status
        update_fields.append('qb_email_status')
    return update_fields


def _serialize_cursor(value):
    if value is None:
        return ''
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value.astimezone(dt_timezone.utc).isoformat(timespec='seconds')


def _cursor_for_query(value):
    parsed = _parse_quickbooks_datetime(value)
    if parsed is None:
        return None
    return _serialize_cursor(parsed - timezone.timedelta(seconds=SYNC_CURSOR_OVERLAP_SECONDS))


def _extract_payload_last_updated_at(payload):
    return _parse_quickbooks_datetime((payload.get('MetaData') or {}).get('LastUpdatedTime'))


def _latest_payload_update(records):
    latest = None
    for record in records:
        updated_at = _extract_payload_last_updated_at(record)
        if updated_at is not None and (latest is None or updated_at > latest):
            latest = updated_at
    return latest


def _nested_value(payload, path, default=None):
    current = payload
    for part in path.split('.'):
        if not isinstance(current, dict):
            return default
        current = current.get(part)
        if current is None:
            return default
    return current


def _payload_needs_update(remote_payload, expected_payload, field_paths):
    for path in field_paths:
        if _nested_value(remote_payload, path, '') != _nested_value(expected_payload, path, ''):
            return True
    return False


def _build_sparse_update_payload(remote_payload, expected_payload):
    payload = dict(expected_payload)
    payload['Id'] = str(remote_payload.get('Id', ''))
    payload['SyncToken'] = str(remote_payload.get('SyncToken', ''))
    payload['sparse'] = True
    return payload


def _summarize_quickbooks_error(error):
    message = str(error or '').strip()
    detail_match = re.search(r"'Detail': '([^']+)'", message)
    if detail_match:
        return detail_match.group(1)
    message_match = re.search(r"'Message': '([^']+)'", message)
    if message_match:
        return message_match.group(1)
    if len(message) > 240:
        return message[:240] + '...'
    return message or _('QuickBooks rejected the item update.')


def _parse_inventory_start_date_setting():
    raw = str(getattr(settings, 'QUICKBOOKS_INVENTORY_START_DATE', '') or '').strip()
    if raw:
        parsed = parse_date(raw)
        if parsed:
            return parsed
    return date(2015, 1, 1)


def _coerce_local_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    if isinstance(value, date):
        return value
    parsed = parse_date(str(value))
    return parsed


def _get_inventory_start_date(*, txn_date=None):
    configured = _parse_inventory_start_date_setting()
    txn_date = _coerce_local_date(txn_date)
    if txn_date is None:
        return configured
    return min(configured, txn_date)


def _prepare_inventory_item_for_txn_date(*, client, presentacion, txn_date, sync_qty_on_hand=True):
    """Ensure a linked inventory item can be used on transactions dated txn_date.

    QuickBooks does not allow changing InvStartDate through the API on existing items,
    so when the stored start date is too late we recreate the item with an earlier one.
    """
    if not sync_qty_on_hand:
        # Outbound transactions (invoice/credit memo) must not recreate items or push
        # local quantities; QuickBooks will adjust inventory when the document posts.
        return

    txn_date = _coerce_local_date(txn_date)
    if txn_date is None:
        return

    qb_id = str(getattr(presentacion, 'quickbooks_id', '') or '').strip()
    if not qb_id:
        return

    remote = client.read_entity('Item', qb_id) or client.find_by_id('Item', qb_id)
    if not remote or not _quickbooks_item_is_inventory(remote):
        return

    inv_start = _coerce_local_date(remote.get('InvStartDate'))
    if inv_start and inv_start <= txn_date:
        return

    desired_payload = _build_item_payload(
        presentacion,
        client=client,
        income_account_ref=remote.get('IncomeAccountRef') or None,
        remote_payload=remote,
        sync_qty_on_hand=sync_qty_on_hand,
    )
    desired_payload['InvStartDate'] = _get_inventory_start_date(txn_date=txn_date).isoformat()

    try:
        recreated = _recreate_presentacion_as_inventory_item(
            presentacion,
            remote,
            desired_payload,
            client=client,
            sync_qty_on_hand=sync_qty_on_hand,
        )
    except QuickBooksAPIError as exc:
        raise QuickBooksSyncError(
            _(
                'QuickBooks item %(item_id)s has inventory start date %(start)s, which is after invoice date %(txn)s, and could not be recreated: %(error)s'
            ) % {
                'item_id': qb_id,
                'start': inv_start.isoformat() if inv_start else '?',
                'txn': txn_date.isoformat(),
                'error': _summarize_quickbooks_error(exc),
            }
        ) from exc

    _mark_synced(presentacion, recreated.get('Id'))
    logger.info(
        'Recreated QuickBooks inventory item %s as %s so txn date %s is allowed.',
        qb_id,
        recreated.get('Id'),
        txn_date.isoformat(),
    )


def _prepare_inventory_items_for_txn_date(*, client, presentaciones, txn_date):
    seen_ids = set()
    for presentacion in presentaciones:
        local_id = getattr(presentacion, 'pk', None)
        dedupe_key = local_id or getattr(presentacion, 'quickbooks_id', None)
        if dedupe_key in seen_ids:
            continue
        seen_ids.add(dedupe_key)
        _prepare_inventory_item_for_txn_date(client=client, presentacion=presentacion, txn_date=txn_date)


def _resolve_txn_date_for_inventory_items(*, client, presentaciones, txn_date):
    """Fallback when inventory items still block the requested transaction date."""
    txn_date = _coerce_local_date(txn_date) or timezone.localdate()
    resolved = txn_date
    for presentacion in presentaciones:
        qb_id = str(getattr(presentacion, 'quickbooks_id', '') or '').strip()
        if not qb_id:
            continue
        remote = client.read_entity('Item', qb_id) or client.find_by_id('Item', qb_id)
        if not remote or not _quickbooks_item_is_inventory(remote):
            continue
        inv_start = _coerce_local_date(remote.get('InvStartDate'))
        if inv_start and inv_start > resolved:
            resolved = inv_start
    return resolved


def _needs_inventory_type_conversion(existing, desired_payload):
    return (
        not _quickbooks_item_is_inventory(existing)
        and str(desired_payload.get('Type') or '').strip().lower() == 'inventory'
    )


def _sync_inventory_qty_after_conversion(*, client, item_payload, presentacion):
    target_qty = _local_presentacion_qty_on_hand(presentacion)
    if target_qty <= 0:
        return item_payload
    try:
        refreshed = client.find_by_id('Item', item_payload.get('Id'))
        if not refreshed:
            return item_payload
        return client.update_item({
            'Id': str(refreshed.get('Id', '')),
            'SyncToken': str(refreshed.get('SyncToken', '')),
            'sparse': True,
            'QtyOnHand': target_qty,
            'InvStartDate': _get_inventory_start_date().isoformat(),
        })
    except QuickBooksAPIError:
        logger.warning(
            'QuickBooks accepted inventory conversion for item %s but rejected QtyOnHand=%s',
            item_payload.get('Id'),
            target_qty,
        )
        return item_payload


def _recreate_presentacion_as_inventory_item(presentacion, existing, desired_payload, *, client, sync_qty_on_hand=True):
    try:
        client.update_item({
            'Id': str(existing.get('Id', '')),
            'SyncToken': str(existing.get('SyncToken', '')),
            'sparse': True,
            'Active': False,
        })
    except QuickBooksAPIError:
        logger.warning('Could not deactivate QuickBooks item %s before inventory recreation.', existing.get('Id'))

    create_payload = dict(desired_payload)
    for key in ('Id', 'SyncToken', 'sparse'):
        create_payload.pop(key, None)
    if not sync_qty_on_hand and 'QtyOnHand' not in create_payload:
        create_payload['QtyOnHand'] = _extract_quickbooks_item_qty_on_hand(existing) or 0

    try:
        created = client.create_item(create_payload)
    except QuickBooksAPIError:
        alt_name = _truncate(f"{create_payload.get('Name', '')} [Inventory]", limit=100)
        create_payload['Name'] = alt_name
        created = client.create_item(create_payload)
    if sync_qty_on_hand:
        return _sync_inventory_qty_after_conversion(client=client, item_payload=created, presentacion=presentacion)
    return created


def _convert_linked_item_to_inventory(presentacion, existing, desired_payload, *, client, sync_qty_on_hand=True):
    conversion_payload = dict(desired_payload)
    conversion_payload['Id'] = str(existing.get('Id', ''))
    conversion_payload['SyncToken'] = str(existing.get('SyncToken', ''))
    conversion_payload['sparse'] = False
    conversion_payload['TrackQtyOnHand'] = True
    if sync_qty_on_hand:
        conversion_payload['QtyOnHand'] = 0
    elif 'QtyOnHand' in conversion_payload:
        conversion_payload.pop('QtyOnHand', None)
    if not conversion_payload.get('PurchaseCost'):
        conversion_payload['PurchaseCost'] = _as_float(presentacion.costo or 0)

    try:
        updated = client.update_item(conversion_payload)
    except QuickBooksAPIError:
        updated = _recreate_presentacion_as_inventory_item(
            presentacion,
            existing,
            desired_payload,
            client=client,
            sync_qty_on_hand=sync_qty_on_hand,
        )
        return updated
    if sync_qty_on_hand:
        return _sync_inventory_qty_after_conversion(client=client, item_payload=updated, presentacion=presentacion)
    return updated


def _mark_synced(instance, quickbooks_id):
    instance.quickbooks_id = str(quickbooks_id or '')
    instance.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    instance.last_synced_at = timezone.now()
    instance.save(update_fields=['quickbooks_id', 'sync_status', 'last_synced_at'])


def _mark_failed(instance):
    instance.sync_status = QUICKBOOKS_SYNC_STATUS_FAILED
    instance.save(update_fields=['sync_status'])


def _sync_result(*, entity, action, payload, **extra):
    result = {
        'entity': entity,
        'action': action,
        'quickbooks_id': str(payload.get('Id', '')),
        'payload': payload,
    }
    if extra:
        result.update(extra)
    return result


def _batch_sync_result(*, record_ids, sync_callable, task_cache_key=None, operation='outbound_batch'):
    results = []
    total = len(record_ids)
    for index, record_id in enumerate(record_ids, start=1):
        try:
            result = sync_callable(record_id)
        except QuickBooksSyncError as exc:
            results.append({
                'id': int(record_id),
                'ok': False,
                'error': str(exc),
            })
        except Exception as exc:
            results.append({
                'id': int(record_id),
                'ok': False,
                'error': str(exc),
            })
        else:
            results.append({
                'id': int(record_id),
                'ok': True,
                'result': result,
            })
        if task_cache_key:
            success_count = sum(1 for item in results if item['ok'])
            failed_count = sum(1 for item in results if not item['ok'])
            progress = int((index / max(total, 1)) * 100)
            cache.set(
                task_cache_key,
                _qb_task_progress_payload(
                    status='running',
                    progress=min(max(progress, 1), 99),
                    operation=operation,
                    result={
                        'processed': index,
                        'total': total,
                        'success_count': success_count,
                        'failed_count': failed_count,
                    },
                ),
                timeout=QB_TASK_PROGRESS_CACHE_TIMEOUT,
            )
    return {
        'requested_ids': [int(record_id) for record_id in record_ids],
        'processed': total,
        'total': total,
        'success_count': sum(1 for item in results if item['ok']),
        'failed_count': sum(1 for item in results if not item['ok']),
        'results': results,
    }


def _strip_ltg_customer_export_prefix(value):
    text = _normalize_text(value)
    if not text:
        return text
    marker = ' - '
    if marker in text:
        prefix, remainder = text.split(marker, 1)
        if prefix.startswith('LTG Customer '):
            return remainder.strip()
    return text


def resolve_customer_company_name(cliente):
    return _truncate(
        _strip_ltg_customer_export_prefix(
            _normalize_text(cliente.nombre_empresa, fallback=f'Cliente {cliente.pk}')
        ),
        limit=255,
    )


def _build_customer_display_name(cliente):
    return resolve_customer_company_name(cliente)


def _resolve_customer_payload_display_name(cliente, *, remote_payload=None):
    if remote_payload:
        for key in ('DisplayName', 'CompanyName', 'PrintOnCheckName'):
            remote_name = _normalize_text(remote_payload.get(key))
            if remote_name:
                return _truncate(remote_name)
    return _build_customer_display_name(cliente)


def _resolve_customer_payload_company_name(cliente, *, remote_payload=None):
    if remote_payload:
        for key in ('CompanyName', 'DisplayName', 'PrintOnCheckName'):
            remote_name = _normalize_text(remote_payload.get(key))
            if remote_name:
                return _truncate(_strip_ltg_customer_export_prefix(remote_name), limit=100)
    return _truncate(_build_customer_display_name(cliente), limit=100)


def _build_customer_payload(cliente, *, remote_payload=None):
    company_name = _resolve_customer_payload_company_name(cliente, remote_payload=remote_payload)
    display_name = _resolve_customer_payload_display_name(cliente, remote_payload=remote_payload)
    payload = {
        'DisplayName': display_name,
        'CompanyName': company_name or display_name,
        'PrintOnCheckName': company_name or display_name,
        'Active': bool(cliente.aprobado),
        'Notes': _truncate(f'Sales tax: {cliente.sales_tax_number}', limit=4000),
    }
    if cliente.telefono:
        payload['PrimaryPhone'] = {'FreeFormNumber': _truncate(cliente.telefono, limit=21)}
    if getattr(cliente.usuario, 'email', ''):
        payload['PrimaryEmailAddr'] = {'Address': _truncate(cliente.usuario.email, limit=100)}
    address = {
        'Line1': _truncate(cliente.direccion, limit=500),
        'City': _truncate(cliente.ciudad, limit=255),
        'CountrySubDivisionCode': _truncate(cliente.estado, limit=255),
        'PostalCode': _truncate(cliente.codigo_postal, limit=30),
        'Country': _truncate(cliente.pais, limit=255),
    }
    clean_address = {key: value for key, value in address.items() if value}
    if clean_address:
        payload['BillAddr'] = clean_address
        payload['ShipAddr'] = clean_address.copy()
    return payload


def fetch_quickbooks_customers(*, max_results=None, client=None, updated_after=None, page_size=100):
    client = client or QuickBooksAPIClient()
    if updated_after:
        return client.find_updated_since('Customer', updated_after, max_results=max_results, page_size=page_size)
    return client.find_all('Customer', max_results=max_results, order_by='MetaData.LastUpdatedTime', page_size=page_size)


def fetch_quickbooks_vendors(*, max_results=25, client=None, updated_after=None, page_size=100):
    client = client or QuickBooksAPIClient()
    if updated_after:
        return client.find_updated_since('Vendor', updated_after, max_results=max_results, page_size=page_size)
    return client.find_all('Vendor', max_results=max_results, order_by='MetaData.LastUpdatedTime', page_size=page_size)


def _extract_quickbooks_vendor_name(payload):
    return _truncate(
        payload.get('DisplayName')
        or payload.get('CompanyName')
        or payload.get('PrintOnCheckName')
        or f"QuickBooks Vendor {payload.get('Id', '')}",
        limit=255,
    )


def _extract_quickbooks_vendor_email(payload):
    return _truncate((payload.get('PrimaryEmailAddr') or {}).get('Address', ''), limit=254)


def _extract_quickbooks_vendor_phone(payload):
    from config.clientes.phone import normalize_stored_phone_number

    raw = (payload.get('PrimaryPhone') or {}).get('FreeFormNumber', '')
    return normalize_stored_phone_number(raw) or _truncate(raw, limit=40)


def _extract_quickbooks_vendor_balance(payload):
    return _quantize_money(payload.get('Balance') or payload.get('OpenBalance') or 0)


def _build_vendor_import_defaults(payload):
    return {
        'nombre': _extract_quickbooks_vendor_name(payload),
        'email': _extract_quickbooks_vendor_email(payload),
        'telefono': _extract_quickbooks_vendor_phone(payload),
        'company_name': _truncate(payload.get('CompanyName') or '', limit=255),
        'balance': _extract_quickbooks_vendor_balance(payload),
        'notas': _truncate(payload.get('Notes') or payload.get('PrintOnCheckName') or '', limit=4000),
        'activo': bool(payload.get('Active', True)),
    }


def _mark_vendor_imported(proveedor, *, quickbooks_id):
    proveedor.quickbooks_id = str(quickbooks_id)
    proveedor.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    proveedor.last_synced_at = timezone.now()
    proveedor.save(update_fields=['quickbooks_id', 'sync_status', 'last_synced_at'])


def _vendor_conflict_exists(*, quickbooks_id, name, email):
    queryset = Proveedor.objects.all()
    if email:
        conflict = queryset.filter(email__iexact=email).exclude(quickbooks_id=quickbooks_id).first()
        if conflict is not None:
            return conflict
    if name:
        conflict = queryset.filter(nombre__iexact=name).exclude(quickbooks_id=quickbooks_id).first()
        if conflict is not None:
            return conflict
    return None


@transaction.atomic
def import_quickbooks_vendor_record(payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks vendor payload is missing an Id.')

    defaults = _build_vendor_import_defaults(payload)
    existing = Proveedor.objects.filter(quickbooks_id=quickbooks_id).first()
    if existing is None:
        conflict = _vendor_conflict_exists(
            quickbooks_id=quickbooks_id,
            name=defaults['nombre'],
            email=defaults['email'],
        )
        if conflict is not None:
            _upsert_import_conflict(
                entity_type=QuickBooksImportConflict.ENTITY_VENDOR,
                quickbooks_id=quickbooks_id,
                display_name=defaults['nombre'],
                reason=f"Local supplier conflict: {conflict.nombre} already exists without QuickBooks linkage.",
                payload=payload,
                local_model='Proveedor',
                local_record_id=conflict.id,
            )
            return {
                'ok': False,
                'action': 'conflict',
                'entity': 'Vendor',
                'quickbooks_id': quickbooks_id,
                'label': defaults['nombre'],
                'error': f"Local supplier conflict: {conflict.nombre} already exists without QuickBooks linkage.",
            }
        proveedor = Proveedor.objects.create(**defaults)
        action = 'created'
    else:
        for field, value in defaults.items():
            setattr(existing, field, value)
        existing.save(update_fields=[*defaults.keys(), 'actualizado_en'])
        proveedor = existing
        action = 'updated'

    _mark_vendor_imported(proveedor, quickbooks_id=quickbooks_id)
    _resolve_import_conflict(
        entity_type=QuickBooksImportConflict.ENTITY_VENDOR,
        quickbooks_id=quickbooks_id,
        local_model='Proveedor',
        local_record_id=proveedor.id,
    )
    return {
        'ok': True,
        'action': action,
        'entity': 'Vendor',
        'quickbooks_id': quickbooks_id,
        'local_id': proveedor.id,
        'label': proveedor.nombre,
    }


def _customer_payload_needs_update(remote_payload, expected_payload):
    return _payload_needs_update(
        remote_payload,
        expected_payload,
        (
            'Active',
            'Notes',
            'PrimaryPhone.FreeFormNumber',
            'PrimaryEmailAddr.Address',
            'BillAddr.Line1',
            'BillAddr.City',
            'BillAddr.CountrySubDivisionCode',
            'BillAddr.PostalCode',
            'BillAddr.Country',
            'ShipAddr.Line1',
            'ShipAddr.City',
            'ShipAddr.CountrySubDivisionCode',
            'ShipAddr.PostalCode',
            'ShipAddr.Country',
        ),
    )


def sync_customer(*, cliente, client=None):
    client = client or QuickBooksAPIClient()
    try:
        if cliente.quickbooks_id:
            existing = client.find_by_id('Customer', cliente.quickbooks_id)
            if existing:
                desired_payload = _build_customer_payload(cliente, remote_payload=existing)
                if _customer_payload_needs_update(existing, desired_payload):
                    updated = client.update_customer(_build_sparse_update_payload(existing, desired_payload))
                    _mark_synced(cliente, updated.get('Id'))
                    return _sync_result(entity='Customer', action='updated', payload=updated)
                _mark_synced(cliente, existing.get('Id'))
                return _sync_result(entity='Customer', action='existing', payload=existing)

        display_name = _build_customer_display_name(cliente)
        existing = client.find_one_by_display_name('Customer', display_name)
        if existing:
            _mark_synced(cliente, existing.get('Id'))
            return _sync_result(entity='Customer', action='linked', payload=existing)

        created = client.create_customer(_build_customer_payload(cliente))
        _mark_synced(cliente, created.get('Id'))
        return _sync_result(entity='Customer', action='created', payload=created)
    except QuickBooksAPIError as exc:
        _mark_failed(cliente)
        raise QuickBooksSyncError(str(exc)) from exc


def _build_item_name(presentacion):
    return _truncate(
        _normalize_text(presentacion.producto.nombre, fallback=f'Producto {presentacion.producto_id}')
    )


def _resolve_item_payload_name(presentacion, *, remote_payload=None):
    if remote_payload:
        remote_name = _normalize_text(remote_payload.get('Name'))
        if remote_name:
            return _truncate(remote_name)
    return _build_item_name(presentacion)


def _build_item_ref(presentacion, *, remote_payload=None):
    return {
        'value': str(getattr(presentacion, 'quickbooks_id', '') or '').strip(),
        'name': _resolve_item_payload_name(presentacion, remote_payload=remote_payload),
    }


def _build_item_description(presentacion):
    parts = [
        _normalize_text(presentacion.producto.descripcion),
        _normalize_text(presentacion.nombre),
        _normalize_text(presentacion.tipo_contenido),
    ]
    description = ' | '.join(part for part in parts if part)
    return _truncate(description, limit=4000)


def _account_ref_from_record(account):
    return {'value': str(account.get('Id')), 'name': account.get('Name', '')}


def _first_account_ref_from_queries(client, queries):
    for query in queries:
        accounts = client.query(query).get('Account', [])
        if accounts:
            return _account_ref_from_record(accounts[0])
    return None


def _account_ref_from_setting(client, setting_name):
    account_id = str(getattr(settings, setting_name, '') or '').strip()
    if not account_id:
        return None
    accounts = client.query(
        f"select Id, Name from Account where Id = '{client._escape_query_value(account_id)}' maxresults 1"
    ).get('Account', [])
    if not accounts:
        raise QuickBooksSyncError(
            f'QuickBooks account {account_id} configured in {setting_name} was not found.'
        )
    return _account_ref_from_record(accounts[0])


def _get_default_income_account_ref(client):
    account_ref = _first_account_ref_from_queries(
        client,
        ("select * from Account where AccountType = 'Income' maxresults 1",),
    )
    if not account_ref:
        raise QuickBooksSyncError('QuickBooks does not have an income account available for item sync.')
    return account_ref


def _get_inventory_income_account_ref(client):
    account_ref = _account_ref_from_setting(client, 'QUICKBOOKS_INVENTORY_INCOME_ACCOUNT_ID')
    if account_ref:
        return account_ref
    account_ref = _first_account_ref_from_queries(
        client,
        (
            "select * from Account where AccountSubType = 'SalesOfProductIncome' maxresults 1",
            "select * from Account where Name = 'Sales of Product Income' maxresults 1",
        ),
    )
    if not account_ref:
        raise QuickBooksSyncError(
            'QuickBooks does not have a Sales of Product Income account available for inventory item sync.'
        )
    return account_ref


def _get_default_expense_account_ref(client):
    account_ref = _account_ref_from_setting(client, 'QUICKBOOKS_INVENTORY_EXPENSE_ACCOUNT_ID')
    if account_ref:
        return account_ref
    account_ref = _first_account_ref_from_queries(
        client,
        (
            "select * from Account where AccountType = 'Cost of Goods Sold' maxresults 1",
            "select * from Account where AccountSubType = 'SuppliesMaterialsCogs' maxresults 1",
        ),
    )
    if not account_ref:
        raise QuickBooksSyncError('QuickBooks does not have a COGS account available for inventory item sync.')
    return account_ref


def _get_default_asset_account_ref(client):
    account_ref = _account_ref_from_setting(client, 'QUICKBOOKS_INVENTORY_ASSET_ACCOUNT_ID')
    if account_ref:
        return account_ref
    account_ref = _first_account_ref_from_queries(
        client,
        (
            "select * from Account where AccountType = 'Other Current Asset' and AccountSubType = 'Inventory' maxresults 1",
            "select * from Account where AccountSubType = 'Inventory' maxresults 1",
            "select * from Account where Name = 'Inventory Asset' maxresults 1",
        ),
    )
    if not account_ref:
        raise QuickBooksSyncError(
            'QuickBooks does not have an inventory asset account available for inventory item sync.'
        )
    return account_ref


def _quickbooks_item_is_inventory(payload):
    return str((payload or {}).get('Type') or '').strip().lower() in {'inventory', 'assembly'}


def _local_presentacion_qty_on_hand(presentacion):
    stock = getattr(presentacion, 'stock_operativo', None)
    if stock is None:
        stock = StockPresentacion.objects.filter(presentacion_id=presentacion.pk).first()
    if stock is None:
        return 0
    return int(stock.stock_fisico or 0)


def _build_item_payload(
    presentacion,
    *,
    client,
    income_account_ref=None,
    remote_payload=None,
    sync_qty_on_hand=True,
    sync_pricing=True,
):
    use_inventory = getattr(settings, 'QUICKBOOKS_USE_INVENTORY_ITEMS', True)
    if remote_payload is not None and not use_inventory:
        # When inventory export is disabled, preserve the existing QuickBooks item type on updates.
        use_inventory = _quickbooks_item_is_inventory(remote_payload)

    # New QB items can seed price/cost/qty from ERP. Updates omit them by default so QuickBooks
    # remains the source of truth for pricing and on-hand quantity.
    include_pricing = sync_pricing or remote_payload is None
    include_qty = sync_qty_on_hand or remote_payload is None

    payload = {
        'Name': _resolve_item_payload_name(presentacion, remote_payload=remote_payload),
        'Active': bool(presentacion.producto.activo),
        'Description': _build_item_description(presentacion),
        'Sku': _truncate(presentacion.producto.codigo_barras, limit=100),
    }
    if include_pricing:
        payload['UnitPrice'] = _as_float(presentacion.precio_3)

    if use_inventory:
        if remote_payload is not None and _quickbooks_item_is_inventory(remote_payload) and income_account_ref:
            income_ref = income_account_ref
        else:
            income_ref = _get_inventory_income_account_ref(client)
    else:
        income_ref = income_account_ref or _get_default_income_account_ref(client)

    if use_inventory:
        inventory_payload = {
            'Type': 'Inventory',
            'TrackQtyOnHand': True,
            'IncomeAccountRef': income_ref,
            'ExpenseAccountRef': _get_default_expense_account_ref(client),
            'AssetAccountRef': _get_default_asset_account_ref(client),
        }
        if include_pricing:
            inventory_payload['PurchaseCost'] = _as_float(presentacion.costo or 0)
        if include_qty:
            inventory_payload['InvStartDate'] = _get_inventory_start_date().isoformat()
            inventory_payload['QtyOnHand'] = _local_presentacion_qty_on_hand(presentacion)
        payload.update(inventory_payload)
    else:
        payload.update({
            'Type': 'NonInventory',
            'IncomeAccountRef': income_ref,
        })
    return payload


def _item_payload_needs_update(remote_payload, expected_payload, *, sync_qty_on_hand=True, sync_pricing=True):
    compare_fields = (
        'Name',
        'Type',
        'Active',
        'Description',
        'IncomeAccountRef.value',
        'Sku',
    )
    if sync_pricing:
        compare_fields = compare_fields + ('UnitPrice',)
    if _quickbooks_item_is_inventory(remote_payload) or str(expected_payload.get('Type') or '').lower() == 'inventory':
        inventory_fields = (
            'ExpenseAccountRef.value',
            'AssetAccountRef.value',
        )
        if sync_pricing:
            inventory_fields = ('PurchaseCost',) + inventory_fields
        if sync_qty_on_hand:
            inventory_fields = ('QtyOnHand',) + inventory_fields
        compare_fields = compare_fields + inventory_fields
    return _payload_needs_update(
        remote_payload,
        expected_payload,
        compare_fields,
    )


CATALOG_PRODUCT_ITEM_TYPES = frozenset({'inventory', 'noninventory', 'assembly'})
# QuickBooks query language does not support parentheses in WHERE clauses.
CATALOG_ACTIVE_ITEMS_WHERE = 'Active = true'


def _is_quickbooks_catalog_product_item(payload):
    item_type = str((payload or {}).get('Type') or '').strip().lower()
    if item_type in {'category', 'service', 'group'}:
        return False
    if not item_type:
        return True
    return item_type in CATALOG_PRODUCT_ITEM_TYPES


def _filter_catalog_import_items(records):
    filtered = []
    for record in records or []:
        if not _is_quickbooks_catalog_product_item(record):
            continue
        filtered.append(record)
    return filtered


def fetch_quickbooks_items(*, max_results=25, client=None, updated_after=None, page_size=None):
    client = client or QuickBooksAPIClient()
    resolved_page_size = page_size or getattr(settings, 'QUICKBOOKS_CATALOG_SYNC_PAGE_SIZE', 1000)
    if updated_after:
        records = client.find_updated_since('Item', updated_after, max_results=max_results, page_size=resolved_page_size)
        return _filter_catalog_import_items(records)
    records = client.find_all(
        'Item',
        max_results=max_results,
        where_clause=CATALOG_ACTIVE_ITEMS_WHERE,
        order_by='MetaData.LastUpdatedTime',
        page_size=resolved_page_size,
    )
    return _filter_catalog_import_items(records)


def _quickbooks_catalog_page_size():
    return min(max(int(getattr(settings, 'QUICKBOOKS_CATALOG_SYNC_PAGE_SIZE', 1000) or 1000), 1), 1000)


def _catalog_sync_skip_images():
    return bool(getattr(settings, 'QUICKBOOKS_CATALOG_SYNC_SKIP_IMAGES', True))


def _fetch_quickbooks_items_by_ids(*, client, item_ids, updated_after=None, chunk_size=100):
    """Fetch specific QuickBooks Items by Id using batched IN queries."""
    found = {}
    ids = [str(item_id).strip() for item_id in item_ids if str(item_id or '').strip()]
    if not ids:
        return found

    chunk_size = max(min(int(chunk_size or 100), _quickbooks_catalog_page_size()), 1)
    for offset in range(0, len(ids), chunk_size):
        chunk = ids[offset:offset + chunk_size]
        in_list = ', '.join(f"'{client._escape_query_value(item_id)}'" for item_id in chunk)
        where_parts = [f"Id IN ({in_list})"]
        if updated_after:
            where_parts.append(f"MetaData.LastUpdatedTime > '{client._escape_query_value(updated_after)}'")
        where_clause = ' AND '.join(where_parts)
        response = client.query(
            client._build_select_statement(
                'Item',
                where_clause=where_clause,
                max_results=min(len(chunk), _quickbooks_catalog_page_size()),
            )
        )
        batch = response.get('Item', [])
        if isinstance(batch, dict):
            batch = [batch]
        for record in batch or []:
            item_id = str(record.get('Id') or '').strip()
            if item_id:
                found[item_id] = record
    return found


def _fetch_quickbooks_items_map(*, client=None, wanted_ids=None, updated_after=None, max_results=None):
    """Fetch QuickBooks Item payloads in paginated bulk queries instead of one API call per item."""
    client = client or QuickBooksAPIClient()
    wanted = {str(item_id).strip() for item_id in (wanted_ids or []) if str(item_id or '').strip()}
    if wanted:
        return _fetch_quickbooks_items_by_ids(
            client=client,
            item_ids=sorted(wanted),
            updated_after=updated_after,
        )

    page_size = _quickbooks_catalog_page_size()
    found = {}
    start_position = 1
    remaining = None if max_results is None else max(int(max_results), 0)

    while True:
        batch_size = page_size if remaining is None else min(page_size, remaining)
        if batch_size <= 0:
            break

        where_clause = None
        if updated_after:
            where_clause = f"MetaData.LastUpdatedTime > '{client._escape_query_value(updated_after)}'"

        response = client.query(
            client._build_select_statement(
                'Item',
                where_clause=where_clause,
                order_by='MetaData.LastUpdatedTime',
                start_position=start_position,
                max_results=batch_size,
            )
        )
        batch = response.get('Item', [])
        if isinstance(batch, dict):
            batch = [batch]
        if not batch:
            break

        for record in batch:
            item_id = str(record.get('Id') or '').strip()
            if item_id:
                found[item_id] = record

        if remaining is not None:
            remaining -= len(batch)
            if remaining <= 0:
                break
        if len(batch) < batch_size:
            break
        start_position += len(batch)

    return found


def _normalize_username_seed(value, fallback='qb-imported-user'):
    normalized = re.sub(r'[^a-z0-9]+', '-', str(value or '').strip().lower()).strip('-')
    return normalized or fallback


def _build_unique_username(seed):
    base_value = _normalize_username_seed(seed)
    candidate = base_value[:150]
    suffix = 1
    while Usuario.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f'{base_value[:140]}-{suffix}'
    return candidate


def _pick_quickbooks_customer_address(payload):
    return payload.get('BillAddr') or payload.get('ShipAddr') or {}


def _extract_quickbooks_customer_display_name(payload):
    return _truncate(
        _strip_ltg_customer_export_prefix(
            payload.get('DisplayName')
            or payload.get('FullyQualifiedName')
            or payload.get('CompanyName')
            or payload.get('PrintOnCheckName')
            or f"QuickBooks Customer {payload.get('Id', '')}"
        ),
        limit=150,
    )


def _extract_quickbooks_customer_company_name(payload):
    return _truncate(
        _strip_ltg_customer_export_prefix(
            payload.get('CompanyName')
            or payload.get('DisplayName')
            or payload.get('PrintOnCheckName')
            or f"QuickBooks Customer {payload.get('Id', '')}"
        ),
        limit=255,
    )


def _extract_quickbooks_customer_name(payload):
    return _extract_quickbooks_customer_company_name(payload)


def _extract_quickbooks_customer_email(payload):
    return _truncate((payload.get('PrimaryEmailAddr') or {}).get('Address', ''), limit=254)


def _extract_quickbooks_customer_phone(payload):
    from config.clientes.phone import normalize_stored_phone_number

    raw = (payload.get('PrimaryPhone') or {}).get('FreeFormNumber', '')
    return normalize_stored_phone_number(raw)


def _extract_quickbooks_customer_balance(payload):
    """QuickBooks Customer balance: positive = customer owes, negative = customer credit."""
    for key in ('BalanceWithJobs', 'Balance', 'OpenBalance'):
        raw = (payload or {}).get(key)
        if raw not in (None, ''):
            return _quantize_money(raw)
    return Decimal('0.00')


def _customer_payload_has_balance(payload):
    return any((payload or {}).get(key) not in (None, '') for key in ('BalanceWithJobs', 'Balance', 'OpenBalance'))


def _enrich_quickbooks_customer_payload(payload, *, client=None):
    normalized = dict(payload or {})
    if _customer_payload_has_balance(normalized):
        return normalized
    customer_id = str(normalized.get('Id') or '').strip()
    if not customer_id:
        return normalized
    client = client or QuickBooksAPIClient()
    full_payload = client.read_entity('Customer', customer_id)
    if not full_payload:
        full_payload = client.find_by_id('Customer', customer_id)
    if not full_payload:
        return normalized
    merged = dict(normalized)
    for key in (
        'Balance',
        'BalanceWithJobs',
        'OpenBalance',
        'Active',
        'DisplayName',
        'CompanyName',
        'PrimaryEmailAddr',
        'PrimaryPhone',
        'BillAddr',
        'ShipAddr',
    ):
        if key in full_payload and full_payload.get(key) not in (None, '', [], {}):
            merged[key] = full_payload[key]
        elif key not in merged or merged.get(key) in (None, '', [], {}):
            if key in full_payload:
                merged[key] = full_payload[key]
    return merged


def _build_customer_import_defaults(payload):
    address = _pick_quickbooks_customer_address(payload)
    return {
        'nombre_empresa': _extract_quickbooks_customer_company_name(payload),
        'telefono': _extract_quickbooks_customer_phone(payload) or '0000000000',
        'direccion': _truncate(address.get('Line1') or 'Imported from QuickBooks', limit=255),
        'ciudad': _truncate(address.get('City') or 'Unknown', limit=100),
        'estado': _truncate(address.get('CountrySubDivisionCode') or 'N/A', limit=100),
        'codigo_postal': _truncate(address.get('PostalCode') or '', limit=20),
        'pais': _truncate(address.get('Country') or 'USA', limit=100),
        'sales_tax_number': _truncate(payload.get('TaxExemptionReasonId') or f"QB-{payload.get('Id', '')}", limit=100),
        'certificado_tax': 'certificados/imported-from-quickbooks.txt',
        'aprobado': True,
        'estado_revision': Cliente.REVIEW_STATUS_APPROVED,
        'balance': _extract_quickbooks_customer_balance(payload),
    }


def _apply_quickbooks_customer_to_local_record(cliente, payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks customer payload is missing an Id.')
    defaults = _build_customer_import_defaults(payload)
    email = _extract_quickbooks_customer_email(payload)
    display_name = _extract_quickbooks_customer_display_name(payload)
    for field, value in defaults.items():
        setattr(cliente, field, value)
    cliente.save(update_fields=list(defaults.keys()))
    user_update_fields = []
    if email and cliente.usuario.email != email:
        cliente.usuario.email = email
        user_update_fields.append('email')
    if display_name and cliente.usuario.first_name != display_name:
        cliente.usuario.first_name = display_name
        user_update_fields.append('first_name')
    if cliente.usuario.last_name:
        cliente.usuario.last_name = ''
        user_update_fields.append('last_name')
    if user_update_fields:
        cliente.usuario.save(update_fields=user_update_fields)
    _mark_customer_imported(cliente, quickbooks_id=quickbooks_id)
    return cliente


def _mark_customer_imported(cliente, *, quickbooks_id):
    cliente.quickbooks_id = str(quickbooks_id)
    cliente.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    cliente.last_synced_at = timezone.now()
    cliente.save(update_fields=['quickbooks_id', 'sync_status', 'last_synced_at'])
    if cliente.usuario.quickbooks_id != str(quickbooks_id):
        cliente.usuario.quickbooks_id = str(quickbooks_id)
        cliente.usuario.save(update_fields=['quickbooks_id'])


@transaction.atomic
def import_quickbooks_customer_record(payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks customer payload is missing an Id.')
    if not _quickbooks_customer_payload_is_importable(payload):
        display_name = _extract_quickbooks_customer_display_name(payload)
        return _skip_import_result(
            entity='Customer',
            quickbooks_id=quickbooks_id,
            label=display_name,
            reason='QuickBooks customer is inactive or deleted.',
        )

    client = QuickBooksAPIClient()
    payload = _enrich_quickbooks_customer_payload(payload, client=client)

    company_name = _extract_quickbooks_customer_company_name(payload)
    display_name = _extract_quickbooks_customer_display_name(payload)
    email = _extract_quickbooks_customer_email(payload)
    defaults = _build_customer_import_defaults(payload)
    existing = Cliente.objects.select_related('usuario').filter(quickbooks_id=quickbooks_id).first()
    if existing is None:
        for name in {company_name, display_name}:
            if not name:
                continue
            existing = Cliente.objects.select_related('usuario').filter(
                Q(nombre_empresa__iexact=name) | Q(usuario__first_name__iexact=name),
            ).filter(Q(quickbooks_id__isnull=True) | Q(quickbooks_id='')).first()
            if existing is not None:
                break
    if existing is None:
        user = Usuario.objects.create_user(
            username=_build_unique_username(f'qb-customer-{quickbooks_id}-{company_name}'),
            email=email,
            first_name=display_name,
            role='cliente',
            is_active=True,
            quickbooks_id=quickbooks_id,
        )
        user.set_unusable_password()
        user.save(update_fields=['password'])
        cliente = Cliente.objects.create(usuario=user, **defaults)
        action = 'created'
    else:
        cliente = _apply_quickbooks_customer_to_local_record(existing, payload)
        action = 'updated'

    if action == 'created':
        _mark_customer_imported(cliente, quickbooks_id=quickbooks_id)
    _resolve_import_conflict(
        entity_type=QuickBooksImportConflict.ENTITY_CUSTOMER,
        quickbooks_id=quickbooks_id,
        local_model='Cliente',
        local_record_id=cliente.id,
    )
    return {
        'ok': True,
        'action': action,
        'entity': 'Customer',
        'quickbooks_id': quickbooks_id,
        'local_id': cliente.id,
        'label': cliente.nombre_empresa,
    }


QUICKBOOKS_IMPORT_PLACEHOLDER_LABEL = 'QuickBooks Imported'


def _is_quickbooks_import_placeholder_name(value):
    return _normalize_text(value).lower() == QUICKBOOKS_IMPORT_PLACEHOLDER_LABEL.lower()


def _is_quickbooks_import_placeholder_relation(obj):
    if obj is None:
        return False
    return _is_quickbooks_import_placeholder_name(getattr(obj, 'nombre', ''))


def _apply_quickbooks_category_and_brand_to_producto(producto, *, category, brand, preserve_local=False):
    if category is not None:
        producto.categoria = category
    elif preserve_local:
        if _is_quickbooks_import_placeholder_relation(producto.categoria):
            producto.categoria = None
    else:
        producto.categoria = None

    if brand is not None:
        producto.marca = brand
    elif preserve_local:
        if _is_quickbooks_import_placeholder_relation(producto.marca):
            producto.marca = None
    else:
        producto.marca = None


def _build_catalog_lookup_cache():
    return {
        'categories': {},
        'brands': {},
        'brand_category_pairs': set(),
    }


def _resolve_quickbooks_item_category_and_brand(payload, *, lookup_cache=None):
    if lookup_cache is not None:
        categories = lookup_cache.setdefault('categories', {})
        brands = lookup_cache.setdefault('brands', {})
        brand_category_pairs = lookup_cache.setdefault('brand_category_pairs', set())
    else:
        categories = brands = brand_category_pairs = None
    hierarchy = _split_quickbooks_item_hierarchy(payload)
    parent_name = _normalize_text((payload.get('ParentRef') or {}).get('name'))
    class_name = _normalize_text((payload.get('ClassRef') or {}).get('name'))
    item_category_type = _normalize_text(payload.get('ItemCategoryType'))
    explicit_brand = _first_populated(
        payload.get('Brand'),
        payload.get('brand'),
        payload.get('Manufacturer'),
        payload.get('manufacturer'),
    )

    category_name = _first_populated(
        class_name,
        parent_name,
        hierarchy[0] if len(hierarchy) >= 2 else '',
        item_category_type if item_category_type and item_category_type.upper() != 'PRODUCT' else '',
    )
    brand_name = explicit_brand
    if not brand_name:
        if len(hierarchy) >= 3:
            brand_name = hierarchy[1]
        elif len(hierarchy) >= 2:
            brand_name = hierarchy[0]
        else:
            name_parts = [part.strip() for part in _normalize_text(payload.get('Name')).split(' - ') if part.strip()]
            if len(name_parts) >= 2:
                brand_name = name_parts[0]

    category = None
    if category_name:
        if categories is not None:
            category = categories.get(category_name)
            if category is None:
                category, _ = Categoria.objects.get_or_create(nombre=category_name)
                categories[category_name] = category
        else:
            category, _ = Categoria.objects.get_or_create(nombre=category_name)

    brand = None
    if brand_name:
        if brands is not None:
            brand = brands.get(brand_name)
            if brand is None:
                brand, _ = Marca.objects.get_or_create(nombre=brand_name)
                brands[brand_name] = brand
        else:
            brand, _ = Marca.objects.get_or_create(nombre=brand_name)
        if category is not None:
            pair_key = (brand.pk, category.pk)
            if brand_category_pairs is None or pair_key not in brand_category_pairs:
                brand.categorias.add(category)
                if brand_category_pairs is not None:
                    brand_category_pairs.add(pair_key)

    return category, brand


def _first_populated(*values):
    for value in values:
        normalized = _normalize_text(value)
        if normalized:
            return normalized
    return ''


def _split_quickbooks_item_hierarchy(payload):
    full_name = _normalize_text(payload.get('FullyQualifiedName'))
    if not full_name:
        return []
    return [part.strip() for part in full_name.split(':') if part.strip()]


def _normalize_packaging_term(value):
    normalized = unicodedata.normalize('NFKD', (value or '').strip().lower())
    return ''.join(char for char in normalized if not unicodedata.combining(char))


def _extract_packaging_unit_count(label):
    if not label:
        return None
    patterns = (
        r'\b(\d+)\s*(?:pk|pack|packs|ct|count|unidades|units|u)\b',
        r'\b(?:case|box|caja|pallet)\s*[- ]*(\d+)\b',
        r'\b(\d+)\s*(?:case|box|caja|pallet)\b',
    )
    for pattern in patterns:
        match = re.search(pattern, label, re.I)
        if match:
            count = int(match.group(1))
            if count > 0:
                return count
    return None


def _resolve_tipo_contenido(label, explicit_tipo=None):
    if explicit_tipo:
        normalized = _normalize_packaging_term(explicit_tipo)
        if normalized in PRESENTACION_TERM_TRANSLATIONS:
            return PRESENTACION_TERM_TRANSLATIONS[normalized]['es']
        return explicit_tipo.strip().lower()

    normalized = _normalize_packaging_term(label)
    if normalized in PRESENTACION_TERM_TRANSLATIONS:
        return PRESENTACION_TERM_TRANSLATIONS[normalized]['es']

    tokens = set(re.findall(r'[a-z0-9]+', normalized))
    packaging_tokens = {
        'case', 'cs', 'ea', 'each', 'pallet', 'plt', 'box', 'bx', 'pack', 'pk', 'bag', 'can', 'bottle',
    }
    packaging_aliases = {
        'case': 'caja',
        'cs': 'caja',
        'ea': 'unidad',
        'each': 'unidad',
        'plt': 'pallet',
        'bx': 'caja',
        'pk': 'pack',
    }
    if tokens & packaging_tokens:
        for token in sorted(tokens, key=len, reverse=True):
            if token in packaging_aliases:
                return packaging_aliases[token]
        for term in sorted(PRESENTACION_TERM_TRANSLATIONS.keys(), key=len, reverse=True):
            if term in tokens:
                return PRESENTACION_TERM_TRANSLATIONS[term]['es']

    return 'unidades'


def _looks_like_packaging_segment(label):
    normalized = _normalize_packaging_term(label)
    if normalized in PRESENTACION_TERM_TRANSLATIONS:
        return True
    if _extract_packaging_unit_count(label):
        return True
    packaging_tokens = {'case', 'cs', 'ea', 'each', 'pallet', 'plt', 'box', 'bx', 'pack', 'pk'}
    tokens = set(re.findall(r'[a-z0-9]+', normalized))
    return bool(tokens & packaging_tokens)


def _parse_description_packaging(description):
    text = _normalize_text(description)
    if not text or ' | ' not in text:
        return None, None
    parts = [part.strip() for part in text.split(' | ') if part.strip()]
    if len(parts) >= 3:
        return parts[1], parts[2]
    if len(parts) == 2:
        return parts[1], None
    return None, None


def _extract_unit_of_measure_label(payload):
    for key in ('UnitOfMeasureRef', 'SalesUnitOfMeasure', 'PurchaseUnitOfMeasure'):
        ref = payload.get(key)
        if isinstance(ref, dict):
            name = ref.get('name') or ref.get('Name') or ref.get('value')
            if name:
                return str(name).strip()
        elif ref:
            return str(ref).strip()
    return ''


def _parse_quickbooks_presentation(payload):
    item_name = _truncate(payload.get('Name') or f"QuickBooks Item {payload.get('Id', '')}", limit=255)
    parts = [part.strip() for part in item_name.split(' - ') if part.strip()]
    desc_pres, desc_tipo = _parse_description_packaging(payload.get('Description') or '')
    uom_label = _extract_unit_of_measure_label(payload)

    product_name = item_name
    presentation_name = desc_pres or uom_label or 'Unit'
    tipo_contenido = desc_tipo
    unidades = 1

    if len(parts) >= 3 and parts[0].startswith('LTG Item '):
        product_name = parts[1]
        presentation_name = parts[2]
    elif len(parts) >= 2 and _looks_like_packaging_segment(parts[-1]):
        product_name = ' - '.join(parts[:-1])
        presentation_name = parts[-1]

    if uom_label and presentation_name in {'Unit', ''}:
        presentation_name = uom_label

    if not tipo_contenido:
        tipo_contenido = _resolve_tipo_contenido(presentation_name)

    unit_count = _extract_packaging_unit_count(presentation_name) or _extract_packaging_unit_count(uom_label)
    if unit_count:
        unidades = unit_count

    from config.productos.packaging import parse_case_packaging_from_product_name
    case_packaging = parse_case_packaging_from_product_name(product_name)
    if case_packaging:
        unidades = case_packaging['units_per_case']
        if presentation_name in {'Unit', 'unidad', 'units', 'unidades', ''}:
            presentation_name = case_packaging['presentation_name']
        if not tipo_contenido or tipo_contenido in {'unidades', 'unidad', 'units', 'unit'}:
            tipo_contenido = case_packaging['content_type']

    from config.productos.packaging import finalize_quickbooks_import_packaging
    finalized = finalize_quickbooks_import_packaging(
        product_name=product_name,
        presentation_name=presentation_name,
        tipo_contenido=tipo_contenido,
        unidades=unidades,
    )
    presentation_name = finalized['presentation_name']
    tipo_contenido = finalized['tipo_contenido']
    unidades = finalized['unidades']

    return (
        _truncate(product_name, limit=255),
        _truncate(presentation_name, limit=100),
        tipo_contenido,
        max(int(unidades), 1),
    )


def _parse_quickbooks_item_name(payload):
    product_name, presentation_name, _, _ = _parse_quickbooks_presentation(payload)
    return product_name, presentation_name


def _extract_quickbooks_item_cost(payload):
    for key in ('PurchaseCost', 'PurchaseCostValue'):
        raw_cost = payload.get(key)
        if raw_cost not in (None, ''):
            return _quantize_money(raw_cost)
    return None


def _extract_quickbooks_item_sales_price(payload):
    """Return QuickBooks Sales Price (API field UnitPrice) when present."""
    for key in ('UnitPrice', 'SalesPrice', 'SalesPriceValue'):
        if key not in (payload or {}):
            continue
        raw_price = payload.get(key)
        if raw_price in (None, ''):
            continue
        return _quantize_money(raw_price)
    return None


def _fetch_quickbooks_item_payload(*, item_id, client=None):
    client = client or QuickBooksAPIClient()
    item_id = str(item_id or '').strip()
    if not item_id:
        return None
    try:
        payload = client.read_entity('Item', item_id)
    except QuickBooksAPIError:
        payload = None
    if payload:
        return payload
    try:
        return client.find_by_id('Item', item_id)
    except QuickBooksAPIError:
        return None


def _is_image_attachable(payload):
    content_type = _normalize_text(payload.get('ContentType')).lower()
    category = _normalize_text(payload.get('Category')).lower()
    file_name = _normalize_text(payload.get('FileName')).lower()
    if content_type.startswith('image/'):
        return True
    if category == 'image':
        return True
    return file_name.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))


def _fetch_quickbooks_item_image(client, payload, *, max_attachments=3):
    item_id = str(payload.get('Id') or '').strip()
    if not item_id:
        return None

    client = client or QuickBooksAPIClient()
    max_attachments = max(int(max_attachments or 3), 1)
    for attachment in client.find_attachments_for_entity('Item', item_id, max_results=max_attachments):
        if not _is_image_attachable(attachment):
            continue
        try:
            file_bytes, content_type = client.download_attachable_content(attachment)
        except QuickBooksAPIError as exc:
            logger.warning('QuickBooks image download failed for item %s: %s', item_id, exc)
            continue
        original_name = attachment.get('FileName') or f'quickbooks-item-{item_id}.bin'
        extension = Path(original_name).suffix
        if not extension:
            content_type = _normalize_text(content_type).lower()
            if content_type == 'image/png':
                extension = '.png'
            elif content_type == 'image/webp':
                extension = '.webp'
            elif content_type == 'image/gif':
                extension = '.gif'
            else:
                extension = '.jpg'
        return ContentFile(file_bytes, name=f'quickbooks-item-{item_id}{extension}')
    logger.info('QuickBooks item %s has no downloadable image attachment.', item_id)
    return None


def _save_quickbooks_item_image(*, producto, payload, client=None, force=False, skip=False, invalidate_catalog_cache=True):
    if skip:
        return False
    if not force and producto.imagen:
        return False
    image_file = _fetch_quickbooks_item_image(client, payload)
    if image_file is None:
        return False
    producto.imagen.save(image_file.name, image_file, save=True)
    if invalidate_catalog_cache:
        cache.delete('catalogo:productos_activos_v2')
    return True


def _extract_quickbooks_item_qty_on_hand(payload):
    # QuickBooks inventory/assembly items expose on-hand quantity; other types do not track stock.
    # Negative QtyOnHand is valid in QuickBooks (oversold / adjustments) and must be preserved locally.
    item_type = str(payload.get('Type') or '').strip().lower()
    if item_type and item_type not in {'inventory', 'assembly'}:
        return None
    for key in ('QtyOnHand', 'QuantityOnHand', 'QtyOnHandValue', 'QuantityOnHandValue'):
        if key not in payload:
            continue
        raw_value = payload.get(key)
        if raw_value is None or raw_value == '':
            continue
        try:
            return int(float(raw_value))
        except (TypeError, ValueError):
            continue
    return None


def _enrich_quickbooks_item_payload(payload, *, client=None):
    normalized = dict(payload or {})
    item_id = str(normalized.get('Id') or '').strip()
    if not item_id:
        return normalized

    item_type = str(normalized.get('Type') or '').strip().lower()
    missing_active = 'Active' not in normalized
    missing_cost = _extract_quickbooks_item_cost(normalized) is None
    missing_qty = (
        item_type in {'inventory', 'assembly'}
        and _extract_quickbooks_item_qty_on_hand(normalized) is None
    )
    if not missing_active and not missing_cost and not missing_qty:
        return normalized

    full_payload = _fetch_quickbooks_item_payload(item_id=item_id, client=client)
    if not full_payload:
        return normalized

    merged = dict(normalized)
    authoritative_keys = {
        'Active',
        'PurchaseCost',
        'PurchaseCostValue',
        'UnitPrice',
        'QtyOnHand',
        'QuantityOnHand',
        'QtyOnHandValue',
        'QuantityOnHandValue',
        'TrackQtyOnHand',
        'Type',
    }
    for key, value in full_payload.items():
        if key in authoritative_keys:
            merged[key] = value
        elif key not in merged or merged.get(key) in (None, '', [], {}):
            merged[key] = value
    return merged


def _sync_stock_from_quickbooks_item(presentacion, payload):
    qty_on_hand = _extract_quickbooks_item_qty_on_hand(payload)
    if qty_on_hand is None:
        return False
    stock, created = StockPresentacion.objects.get_or_create(
        presentacion=presentacion,
        defaults={'stock_fisico': qty_on_hand, 'stock_reservado': 0, 'stock_disponible': qty_on_hand},
    )
    if created:
        return True
    if stock.stock_fisico == qty_on_hand and stock.computed_stock_disponible() == stock.stock_disponible:
        return False
    stock.stock_fisico = qty_on_hand
    stock.stock_disponible = stock.computed_stock_disponible()
    stock.save(update_fields=['stock_fisico', 'stock_disponible', 'actualizado_en'])
    return True


def _update_presentacion_from_quickbooks(
    presentacion,
    *,
    quickbooks_id,
    item_cost,
    sales_price=None,
    presentation_name=None,
    tipo_contenido=None,
    unidades=None,
):
    presentacion.quickbooks_id = quickbooks_id
    presentacion.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    presentacion.last_synced_at = timezone.now()
    update_fields = ['quickbooks_id', 'sync_status', 'last_synced_at']
    if item_cost is not None:
        presentacion.costo = item_cost
        update_fields.append('costo')
    if sales_price is not None:
        presentacion.qb_price = sales_price
        update_fields.append('qb_price')
    if presentation_name is not None:
        presentacion.nombre = presentation_name
        update_fields.append('nombre')
    if tipo_contenido is not None:
        presentacion.tipo_contenido = tipo_contenido
        update_fields.append('tipo_contenido')
    if unidades is not None:
        presentacion.unidades = unidades
        update_fields.append('unidades')
    presentacion.save(update_fields=update_fields)


def _product_conflict_exists(*, quickbooks_id, product_name, presentation_name):
    return Presentacion.objects.select_related('producto').filter(
        producto__nombre__iexact=product_name,
        nombre__iexact=presentation_name,
    ).exclude(quickbooks_id=quickbooks_id).first()


def _apply_quickbooks_item_to_local_record(
    presentacion,
    payload,
    *,
    client=None,
    skip_images=False,
    lookup_cache=None,
    force_active_refresh=False,
):
    quickbooks_id = str(payload.get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks item payload is missing an Id.')
    client = client or QuickBooksAPIClient()
    product_name, presentation_name, tipo_contenido, unidades = _parse_quickbooks_presentation(payload)
    description = _truncate(payload.get('Description') or payload.get('FullyQualifiedName') or '', limit=4000)
    sku = _truncate(payload.get('Sku') or '', limit=100)
    item_cost = _extract_quickbooks_item_cost(payload)
    sales_price = _extract_quickbooks_item_sales_price(payload)
    category, brand = _resolve_quickbooks_item_category_and_brand(payload, lookup_cache=lookup_cache)
    producto = presentacion.producto
    image_saved = _save_quickbooks_item_image(producto=producto, payload=payload, client=client, skip=skip_images)
    producto.nombre = product_name
    producto.descripcion = description
    _apply_quickbooks_category_and_brand_to_producto(
        producto,
        category=category,
        brand=brand,
        preserve_local=True,
    )
    producto.activo = _resolve_quickbooks_item_active(
        payload,
        client=client,
        fetch_when_missing=True,
        force_refresh=force_active_refresh,
    )
    if sku:
        if producto.codigo_barras == sku:
            pass
        elif Producto.objects.exclude(pk=producto.pk).filter(codigo_barras=sku).exists():
            conflicting = Producto.objects.exclude(pk=producto.pk).filter(codigo_barras=sku).first()
            _upsert_import_conflict(
                entity_type=QuickBooksImportConflict.ENTITY_ITEM,
                quickbooks_id=f'{quickbooks_id}-barcode-omitted',
                display_name=payload.get('Name') or quickbooks_id,
                reason=(
                    f'Duplicate codigo_barras {sku!r} when updating linked item; '
                    f'kept existing barcode for review.'
                ),
                payload=payload,
                local_model='Producto',
                local_record_id=(conflicting.id if conflicting is not None else producto.id),
            )
        else:
            producto.codigo_barras = sku
    producto.quickbooks_id = quickbooks_id
    producto.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    producto.last_synced_at = timezone.now()
    product_update_fields = ['nombre', 'descripcion', 'categoria', 'marca', 'activo', 'codigo_barras', 'quickbooks_id', 'sync_status', 'last_synced_at']
    if image_saved:
        product_update_fields.append('imagen')
    producto.save(update_fields=product_update_fields)
    _update_presentacion_from_quickbooks(
        presentacion,
        quickbooks_id=quickbooks_id,
        item_cost=item_cost,
        sales_price=sales_price,
        presentation_name=presentation_name,
        tipo_contenido=tipo_contenido,
        unidades=unidades,
    )
    from config.productos.packaging import apply_case_packaging_defaults_to_presentacion
    if apply_case_packaging_defaults_to_presentacion(presentacion, product_name):
        presentacion.save(update_fields=['nombre', 'tipo_contenido', 'unidades'])
    try:
        _sync_stock_from_quickbooks_item(presentacion, payload)
    except Exception:
        pass
    return presentacion


def _skip_or_deactivate_deleted_quickbooks_item(*, payload, quickbooks_id, prefetched_presentacion=None):
    """Skip deleted QB items; deactivate any already-linked local catalog product."""
    deactivated = _deactivate_local_product_for_quickbooks_item(
        quickbooks_id=quickbooks_id,
        prefetched_presentacion=prefetched_presentacion,
    )
    if deactivated is not None:
        producto = deactivated.producto
        label = (
            f'{producto.nombre} / {deactivated.nombre}'
            if producto is not None
            else ((payload or {}).get('Name') or quickbooks_id)
        )
        return {
            'ok': True,
            'action': 'deactivated',
            'entity': 'Item',
            'quickbooks_id': quickbooks_id,
            'local_id': deactivated.id,
            'label': label,
            'reason': 'QuickBooks item is deleted.',
        }
    return _skip_import_result(
        entity='Item',
        quickbooks_id=quickbooks_id,
        label=(payload or {}).get('Name') or quickbooks_id,
        reason='QuickBooks item is deleted.',
    )


@transaction.atomic
def import_quickbooks_item_record(
    payload,
    *,
    client=None,
    skip_enrich=False,
    skip_images=None,
    prefetched_presentacion=None,
    lookup_cache=None,
    force_active_refresh=False,
):
    quickbooks_id = str((payload or {}).get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks item payload is missing an Id.')
    if not _quickbooks_item_payload_is_importable(payload):
        return _skip_or_deactivate_deleted_quickbooks_item(
            payload=payload,
            quickbooks_id=quickbooks_id,
            prefetched_presentacion=prefetched_presentacion,
        )

    if skip_images is None:
        skip_images = _catalog_sync_skip_images()
    if not skip_enrich:
        payload = _enrich_quickbooks_item_payload(payload, client=client)
    else:
        payload = dict(payload or {})
    if not _quickbooks_item_payload_is_importable(payload):
        return _skip_or_deactivate_deleted_quickbooks_item(
            payload=payload,
            quickbooks_id=quickbooks_id,
            prefetched_presentacion=prefetched_presentacion,
        )
    product_name, presentation_name, tipo_contenido, unidades = _parse_quickbooks_presentation(payload)
    description = _truncate(payload.get('Description') or payload.get('FullyQualifiedName') or '', limit=4000)
    sku = _truncate(payload.get('Sku') or '', limit=100)
    unit_price = _extract_quickbooks_item_sales_price(payload)
    item_cost = _extract_quickbooks_item_cost(payload)
    category, brand = _resolve_quickbooks_item_category_and_brand(payload, lookup_cache=lookup_cache)
    existing = _find_local_presentacion_for_quickbooks_item(
        quickbooks_id,
        prefetched_presentacion=prefetched_presentacion,
    )
    item_active = _resolve_quickbooks_item_active(
        payload,
        client=client,
        fetch_when_missing=False,
        force_refresh=False,
    )
    if not item_active and existing is None:
        return _skip_import_result(
            entity='Item',
            quickbooks_id=quickbooks_id,
            label=payload.get('Name') or quickbooks_id,
            reason='QuickBooks item is inactive.',
        )
    client = client or QuickBooksAPIClient()
    item_active = _resolve_quickbooks_item_active(
        payload,
        client=client,
        fetch_when_missing=existing is not None,
        force_refresh=force_active_refresh,
    )
    if not item_active and existing is None:
        return _skip_import_result(
            entity='Item',
            quickbooks_id=quickbooks_id,
            label=payload.get('Name') or quickbooks_id,
            reason='QuickBooks item is inactive.',
        )
    if existing is None:
        conflict = _product_conflict_exists(
            quickbooks_id=quickbooks_id,
            product_name=product_name,
            presentation_name=presentation_name,
        )
        if conflict is not None:
            _upsert_import_conflict(
                entity_type=QuickBooksImportConflict.ENTITY_ITEM,
                quickbooks_id=quickbooks_id,
                display_name=payload.get('Name') or quickbooks_id,
                reason=f'Local catalog conflict: {conflict.producto.nombre} / {conflict.nombre} already exists without QuickBooks linkage.',
                payload=payload,
                local_model='Presentacion',
                local_record_id=conflict.id,
            )
            return {
                'ok': False,
                'action': 'conflict',
                'entity': 'Item',
                'quickbooks_id': quickbooks_id,
                'label': payload.get('Name') or quickbooks_id,
                'error': f'Local catalog conflict: {conflict.producto.nombre} / {conflict.nombre} already exists without QuickBooks linkage.',
            }

        # Wrap creation flow to handle integrity errors (duplicate codigo_barras)
        try:
            if sku and Producto.objects.filter(codigo_barras=sku).exists():
                existing_prod = Producto.objects.filter(codigo_barras=sku).first()
                original_sku = sku
                sku = ''
                QuickBooksImportConflict.objects.create(
                    entity_type=QuickBooksImportConflict.ENTITY_ITEM,
                    quickbooks_id=f"{quickbooks_id}-barcode-omitted",
                    display_name=payload.get('Name') or quickbooks_id,
                    reason=f'Local producto with codigo_barras {original_sku} exists (id={existing_prod.id}); importing without codigo_barras for review.',
                    payload=payload,
                    local_model='Producto',
                    local_record_id=existing_prod.id,
                )
            producto = Producto.objects.create(
                nombre=product_name,
                descripcion=description,
                categoria=category,
                marca=brand,
                codigo_barras=sku or None,
                activo=item_active,
                quickbooks_id=quickbooks_id,
                sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
                last_synced_at=timezone.now(),
            )
            _save_quickbooks_item_image(producto=producto, payload=payload, client=client, skip=skip_images)
            presentacion = Presentacion.objects.create(
                producto=producto,
                nombre=presentation_name,
                unidades=unidades,
                tipo_contenido=tipo_contenido,
                quickbooks_id=quickbooks_id,
                sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
                last_synced_at=timezone.now(),
                costo=item_cost,
                qb_price=unit_price,
            )
            try:
                _sync_stock_from_quickbooks_item(presentacion, payload)
            except Exception:
                pass
            action = 'created'
        except IntegrityError as exc:
            err_text = str(exc)
            if 'codigo_barras' in err_text or 'productos_producto.codigo_barras' in err_text:
                # Retry creation without codigo_barras
                if sku:
                    sku = ''
                producto = Producto.objects.create(
                    nombre=product_name,
                    descripcion=description,
                    categoria=category,
                    marca=brand,
                    codigo_barras=None,
                    activo=item_active,
                    quickbooks_id=quickbooks_id,
                    sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
                    last_synced_at=timezone.now(),
                )
                # Record that we imported this QuickBooks item but omitted its codigo_barras
                QuickBooksImportConflict.objects.create(
                    entity_type=QuickBooksImportConflict.ENTITY_ITEM,
                    quickbooks_id=f"{quickbooks_id}-barcode-omitted",
                    display_name=payload.get('Name') or quickbooks_id,
                    reason=f'Duplicate codigo_barras detected; imported item without codigo_barras for review.',
                    payload=payload,
                    local_model='Producto',
                    local_record_id=producto.id,
                )
                _save_quickbooks_item_image(producto=producto, payload=payload, client=client, skip=skip_images)
                presentacion = Presentacion.objects.create(
                    producto=producto,
                    nombre=presentation_name,
                    unidades=unidades,
                    tipo_contenido=tipo_contenido,
                    quickbooks_id=quickbooks_id,
                    sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
                    last_synced_at=timezone.now(),
                    costo=item_cost,
                    qb_price=unit_price,
                )
                try:
                    _sync_stock_from_quickbooks_item(presentacion, payload)
                except Exception:
                    pass
                action = 'created'
            else:
                raise
    else:
        presentacion = _apply_quickbooks_item_to_local_record(
            existing,
            payload,
            client=client,
            skip_images=skip_images,
            lookup_cache=lookup_cache,
            force_active_refresh=force_active_refresh,
        )
        producto = presentacion.producto
        action = 'updated'

    _resolve_import_conflict(
        entity_type=QuickBooksImportConflict.ENTITY_ITEM,
        quickbooks_id=quickbooks_id,
        local_model='Presentacion',
        local_record_id=presentacion.id,
    )
    return {
        'ok': True,
        'action': action,
        'entity': 'Item',
        'quickbooks_id': quickbooks_id,
        'local_id': presentacion.id,
        'label': f'{producto.nombre} / {presentacion.nombre}',
    }


def _deactivate_local_product_for_quickbooks_item(*, quickbooks_id, prefetched_presentacion=None):
    qb_id = str(quickbooks_id or '').strip()
    if not qb_id:
        return None
    presentacion = _find_local_presentacion_for_quickbooks_item(
        qb_id,
        prefetched_presentacion=prefetched_presentacion,
    )
    if presentacion is None:
        return None
    producto = presentacion.producto
    if producto is None or not producto.activo:
        return presentacion
    producto.activo = False
    producto.save(update_fields=['activo'])
    cache.delete('catalogo:productos_activos_v2')
    return presentacion


def _merge_import_batch_results(*results):
    merged = {
        'entity': 'CatalogSync',
        'count': 0,
        'created_count': 0,
        'updated_count': 0,
        'deactivated_count': 0,
        'skipped_count': 0,
        'conflict_count': 0,
        'failed_count': 0,
        'latest_updated_at': None,
        'results': [],
    }
    latest_dt = None
    for result in results or []:
        if not result:
            continue
        merged['count'] += int(result.get('count') or 0)
        merged['created_count'] += int(result.get('created_count') or 0)
        merged['updated_count'] += int(result.get('updated_count') or 0)
        merged['deactivated_count'] += int(result.get('deactivated_count') or 0)
        merged['skipped_count'] += int(result.get('skipped_count') or 0)
        merged['conflict_count'] += int(result.get('conflict_count') or 0)
        merged['failed_count'] += int(result.get('failed_count') or 0)
        merged['results'].extend(result.get('results') or [])
        candidate = _parse_quickbooks_datetime(result.get('latest_updated_at'))
        if candidate and (latest_dt is None or candidate > latest_dt):
            latest_dt = candidate
            merged['latest_updated_at'] = result.get('latest_updated_at')
    return merged


QB_TASK_PROGRESS_CACHE_TIMEOUT = 60 * 60
QB_TASK_STALE_AFTER_SECONDS = 180


def _qb_task_progress_payload(*, status, progress, operation, result=None, error=None):
    payload = {
        'status': status,
        'progress': progress,
        'operation': operation,
        'updated_at': time.time(),
    }
    if result is not None:
        payload['result'] = result
    if error is not None:
        payload['error'] = error
    return payload


def _empty_import_batch_summary(*, entity_name):
    return {
        'entity': entity_name,
        'count': 0,
        'created_count': 0,
        'updated_count': 0,
        'deactivated_count': 0,
        'skipped_count': 0,
        'conflict_count': 0,
        'failed_count': 0,
        'latest_updated_at': '',
        'results': [],
    }


def _merge_import_batch_summaries(base_summary, page_summary):
    merged = dict(base_summary)
    merged['count'] = int(base_summary.get('count') or 0) + int(page_summary.get('count') or 0)
    for key in ('created_count', 'updated_count', 'deactivated_count', 'skipped_count', 'conflict_count', 'failed_count'):
        merged[key] = int(base_summary.get(key) or 0) + int(page_summary.get(key) or 0)
    page_latest = page_summary.get('latest_updated_at') or ''
    base_latest = base_summary.get('latest_updated_at') or ''
    if page_latest and (not base_latest or page_latest > base_latest):
        merged['latest_updated_at'] = page_latest
    merged['results'] = list(base_summary.get('results') or []) + list(page_summary.get('results') or [])
    return merged


def _import_batch_result(*, entity_name, records, import_callable, task_cache_key=None, client=None, progress_offset=0, progress_total=None):
    results = []
    total = progress_total if progress_total is not None else len(records or [])
    processed = 0
    batch_client = client
    # initialize progress if we have a task key
    if task_cache_key:
        cache.set(
            task_cache_key,
            _qb_task_progress_payload(
                status='running',
                progress=0,
                operation=entity_name,
                result={'processed': progress_offset, 'total': total},
            ),
            timeout=QB_TASK_PROGRESS_CACHE_TIMEOUT,
        )
    for record in records:
        if batch_client and processed and processed % 100 == 0:
            try:
                batch_client.connection = ensure_valid_access_token(connection=batch_client.connection, force_refresh=True)
            except QuickBooksServiceError:
                pass
        try:
            with transaction.atomic():
                result = import_callable(record)
        except Exception as exc:
            # If the failure looks like a duplicate barcode IntegrityError,
            # try once more with the item's Sku cleared to avoid unique constraint.
            err_text = str(exc)
            if 'codigo_barras' in err_text or 'productos_producto.codigo_barras' in err_text:
                try:
                    retry_record = dict(record)
                    retry_record['Sku'] = ''
                    with transaction.atomic():
                        result = import_callable(retry_record)
                    results.append(result)
                    continue
                except Exception:
                    # fall through to append failed result below
                    pass
            results.append({
                'ok': False,
                'action': 'failed',
                'entity': entity_name,
                'quickbooks_id': str(record.get('Id') or ''),
                'label': record.get('DisplayName') or record.get('Name') or record.get('Id') or entity_name,
                'error': str(exc),
            })
        else:
            results.append(result)

        # update progress in cache after each record (throttled for large batches)
        processed += 1
        processed_global = progress_offset + processed
        progress_interval = 1 if total <= 100 else 5 if total <= 500 else 10
        if task_cache_key and total > 0 and (processed_global == total or processed % progress_interval == 0):
            # keep some headroom for finalization; map processed/total to 5..95
            pct = int((processed_global / total) * 90) + 5
            pct = min(max(pct, 0), 95)
            cache.set(
                task_cache_key,
                _qb_task_progress_payload(
                    status='running',
                    progress=pct,
                    operation=entity_name,
                    result={'processed': processed_global, 'total': total},
                ),
                timeout=QB_TASK_PROGRESS_CACHE_TIMEOUT,
            )

    return {
        'entity': entity_name,
        'count': len(records),
        'created_count': sum(1 for item in results if item.get('ok') and item.get('action') == 'created'),
        'updated_count': sum(1 for item in results if item.get('ok') and item.get('action') == 'updated'),
        'deactivated_count': sum(1 for item in results if item.get('ok') and item.get('action') == 'deactivated'),
        'skipped_count': sum(1 for item in results if item.get('action') == 'skipped'),
        'conflict_count': sum(1 for item in results if item.get('action') == 'conflict'),
        'failed_count': sum(1 for item in results if not item.get('ok') and item.get('action') not in {'conflict', 'skipped'}),
        'latest_updated_at': _serialize_cursor(_latest_payload_update(records)),
        'results': results,
    }


def _upsert_import_conflict(*, entity_type, quickbooks_id, doc_number='', display_name='', reason='', payload=None, local_model='', local_record_id=None):
    conflict, _ = QuickBooksImportConflict.objects.get_or_create(
        entity_type=entity_type,
        quickbooks_id=str(quickbooks_id),
        defaults={
            'doc_number': doc_number,
            'display_name': display_name,
            'reason': reason,
            'payload': payload or {},
            'local_model': local_model,
            'local_record_id': local_record_id,
        },
    )
    conflict.doc_number = doc_number
    conflict.display_name = display_name
    conflict.status = QuickBooksImportConflict.STATUS_CONFLICT
    conflict.reason = reason
    conflict.payload = payload or {}
    conflict.local_model = local_model
    conflict.local_record_id = local_record_id
    conflict.resolution_note = ''
    conflict.resolved_by = None
    conflict.resolved_at = None
    conflict.save(update_fields=['doc_number', 'display_name', 'status', 'reason', 'payload', 'local_model', 'local_record_id', 'resolution_note', 'resolved_by', 'resolved_at'])
    return conflict


def _resolve_import_conflict(*, entity_type, quickbooks_id, local_model, local_record_id, user=None, resolution_note=''):
    conflict = QuickBooksImportConflict.objects.filter(entity_type=entity_type, quickbooks_id=str(quickbooks_id)).first()
    if conflict is None:
        return None
    conflict.status = QuickBooksImportConflict.STATUS_MATCHED
    conflict.reason = ''
    conflict.local_model = local_model
    conflict.local_record_id = local_record_id
    conflict.resolution_note = resolution_note
    conflict.resolved_by = user
    conflict.resolved_at = timezone.now()
    conflict.save(update_fields=['status', 'reason', 'local_model', 'local_record_id', 'resolution_note', 'resolved_by', 'resolved_at'])
    return conflict


def dismiss_quickbooks_import_conflict(conflict, *, user=None, resolution_note=''):
    conflict.status = QuickBooksImportConflict.STATUS_DISMISSED
    conflict.resolution_note = resolution_note
    conflict.resolved_by = user
    conflict.resolved_at = timezone.now()
    conflict.save(update_fields=['status', 'resolution_note', 'resolved_by', 'resolved_at'])
    return conflict


def dismiss_quickbooks_import_conflicts_bulk(*, conflict_ids, user=None, resolution_note=''):
    conflicts = list(
        QuickBooksImportConflict.objects.filter(
            pk__in=conflict_ids,
            status=QuickBooksImportConflict.STATUS_CONFLICT,
        )
    )
    if not conflicts:
        return 0

    resolved_at = timezone.now()
    for conflict in conflicts:
        conflict.status = QuickBooksImportConflict.STATUS_DISMISSED
        conflict.resolution_note = resolution_note
        conflict.resolved_by = user
        conflict.resolved_at = resolved_at

    QuickBooksImportConflict.objects.bulk_update(
        conflicts,
        ['status', 'resolution_note', 'resolved_by', 'resolved_at'],
    )
    return len(conflicts)


def import_quickbooks_customers(*, max_results=None, client=None, updated_after=None, task_cache_key=None):
    records = fetch_quickbooks_customers(max_results=max_results, client=client, updated_after=updated_after)
    return _import_batch_result(entity_name='Customer', records=records, import_callable=import_quickbooks_customer_record, task_cache_key=task_cache_key)


def import_quickbooks_vendors(*, max_results=25, client=None, updated_after=None, task_cache_key=None):
    records = fetch_quickbooks_vendors(max_results=max_results, client=client, updated_after=updated_after)
    return _import_batch_result(entity_name='Vendor', records=records, import_callable=import_quickbooks_vendor_record, task_cache_key=task_cache_key)


def import_quickbooks_items(*, max_results=25, client=None, updated_after=None, task_cache_key=None, skip_images=None):
    if skip_images is None:
        skip_images = _catalog_sync_skip_images()
    records = fetch_quickbooks_items(max_results=max_results, client=client, updated_after=updated_after)
    # Keep the full set of QuickBooks ids we received so we can detect
    # Items that were removed/disabled in QuickBooks and mark them
    # as inactive locally after the import run.
    if records:
        all_ids = {str(r.get('Id') or '') for r in records}
        filtered = _filter_catalog_import_items(records)
    else:
        all_ids = set()
        filtered = []

    prefetched_presentaciones = {
        str(presentacion.quickbooks_id): presentacion
        for presentacion in Presentacion.objects.select_related('producto').filter(quickbooks_id__in=all_ids)
    }
    lookup_cache = _build_catalog_lookup_cache()

    result = _import_batch_result(
        entity_name='Item',
        records=filtered,
        import_callable=lambda record: import_quickbooks_item_record(
            record,
            client=client,
            skip_enrich=True,
            skip_images=skip_images,
            prefetched_presentacion=prefetched_presentaciones.get(str(record.get('Id') or '').strip()),
            lookup_cache=lookup_cache,
        ),
        task_cache_key=task_cache_key,
        client=client,
    )

    # If QuickBooks no longer returns some linked items, mark their local
    # `Producto` as inactive to reflect deletion/disable in QuickBooks.
    # Only mark missing items inactive after a full unlimited catalog pull.
    # Incremental or capped imports would otherwise deactivate linked products
    # that simply were not included in the current batch.
    deactivate_missing_items = updated_after is None and max_results is None
    try:
        if deactivate_missing_items and all_ids:
            missing_qb_pres = (
                Presentacion.objects.select_related('producto')
                .filter(quickbooks_id__isnull=False)
                .exclude(quickbooks_id__in=all_ids)
            )
            disabled = []
            products_to_disable = []
            for pres in missing_qb_pres:
                prod = pres.producto
                if prod and prod.activo:
                    prod.activo = False
                    products_to_disable.append(prod)
                    disabled.append({'quickbooks_id': pres.quickbooks_id, 'local_id': prod.id, 'label': prod.nombre})
            if products_to_disable:
                Producto.objects.bulk_update(products_to_disable, ['activo'])
            if disabled:
                result['disabled_count'] = len(disabled)
                result['disabled'] = disabled
    except Exception:
        # Don't let this block the main import result if something goes wrong
        pass

    cache.delete('catalogo:productos_activos_v2')
    return result


def sync_missing_quickbooks_item_images(*, limit=None, dry_run=False, client=None, task_cache_key=None):
    client = client or QuickBooksAPIClient()
    queryset = _products_missing_quickbooks_images_queryset()
    if limit is not None:
        queryset = queryset[:max(int(limit), 0)]
    total = queryset.count()

    summary = {
        'checked': 0,
        'synced': 0,
        'missing_in_qb': 0,
        'failed': 0,
        'synced_labels': [],
    }
    for index, producto in enumerate(queryset.iterator(), start=1):
        summary['checked'] += 1
        item_id = _resolve_product_quickbooks_item_id(producto)
        if not item_id:
            summary['missing_in_qb'] += 1
            continue
        try:
            payload = client.find_by_id('Item', item_id) or {'Id': item_id, 'Name': producto.nombre}
        except QuickBooksAPIError:
            summary['failed'] += 1
            continue

        if dry_run:
            attachments = client.find_attachments_for_entity('Item', item_id, max_results=3)
            image_attachments = [attachment for attachment in attachments if _is_image_attachable(attachment)]
            if image_attachments:
                summary['synced'] += 1
                summary['synced_labels'].append(producto.nombre)
            else:
                summary['missing_in_qb'] += 1
            continue

        if _save_quickbooks_item_image(
            producto=producto,
            payload=payload,
            client=client,
            invalidate_catalog_cache=False,
        ):
            summary['synced'] += 1
            summary['synced_labels'].append(producto.nombre)
        else:
            summary['missing_in_qb'] += 1

        if task_cache_key and total:
            task_state = cache.get(task_cache_key) or {}
            task_state['status'] = 'running'
            task_state['progress'] = int(index * 100 / total)
            cache.set(task_cache_key, task_state, timeout=60 * 60)

    cache.delete('catalogo:productos_activos_v2')
    return summary


def pull_quickbooks_item_images_to_local(*, limit=None, client=None, task_cache_key=None):
    return sync_missing_quickbooks_item_images(
        limit=limit,
        client=client,
        task_cache_key=task_cache_key,
    )


def _quickbooks_id_for_presentacion(presentacion):
    for raw_id in (presentacion.quickbooks_id, getattr(presentacion.producto, 'quickbooks_id', None)):
        qb_id = str(raw_id or '').strip()
        if qb_id:
            return qb_id
    return ''


def import_quickbooks_inventory_quantities(*, limit=None, max_results=None, client=None, task_cache_key=None):
    """Update local physical stock from QuickBooks QtyOnHand for linked catalog rows only."""
    if limit is None:
        limit = max_results
    client = client or QuickBooksAPIClient()

    queryset = _linked_catalog_presentacion_queryset()
    if limit is not None:
        queryset = queryset[:max(int(limit), 0)]

    linked_presentaciones = list(queryset)
    total = len(linked_presentaciones)
    if total == 0:
        return {
            'entity': 'InventoryQuantity',
            'count': 0,
            'processed': 0,
            'total': 0,
            'updated_count': 0,
            'skipped_count': 0,
            'failed_count': 0,
            'results': [],
        }

    qb_ids = _linked_catalog_quickbooks_ids(linked_presentaciones)
    items_map = _fetch_quickbooks_items_map(client=client, wanted_ids=qb_ids)

    if task_cache_key:
        cache.set(
            task_cache_key,
            {
                'status': 'running',
                'progress': 5,
                'operation': 'import_inventory_quantities_to_local',
                'result': {'processed': 0, 'total': total},
            },
            timeout=60 * 60,
        )

    results = []
    updated_count = 0
    skipped_count = 0
    failed_count = 0
    processed = 0
    progress_interval = 1 if total <= 100 else 5 if total <= 500 else 10

    for presentacion in linked_presentaciones:
        qb_id = _quickbooks_id_for_presentacion(presentacion)
        label = f'{presentacion.producto.nombre} / {presentacion.nombre}'

        if not qb_id:
            skipped_count += 1
            processed += 1
            continue

        payload = items_map.get(qb_id)
        if not payload:
            failed_count += 1
            results.append({
                'ok': False,
                'action': 'missing',
                'entity': 'InventoryQuantity',
                'quickbooks_id': qb_id,
                'label': label,
                'error': 'Item not found in QuickBooks.',
            })
            processed += 1
            continue

        try:
            with transaction.atomic():
                changed = _sync_stock_from_quickbooks_item(presentacion, payload)
        except Exception as exc:
            failed_count += 1
            results.append({
                'ok': False,
                'action': 'failed',
                'entity': 'InventoryQuantity',
                'quickbooks_id': qb_id,
                'label': label,
                'error': str(exc),
            })
        else:
            if changed:
                updated_count += 1
            else:
                skipped_count += 1

        processed += 1
        if task_cache_key and total > 0 and (processed == total or processed % progress_interval == 0):
            pct = int((processed / total) * 90) + 5
            pct = min(max(pct, 0), 95)
            cache.set(
                task_cache_key,
                {
                    'status': 'running',
                    'progress': pct,
                    'operation': 'import_inventory_quantities_to_local',
                    'result': {'processed': processed, 'total': total},
                },
                timeout=60 * 60,
            )

    return {
        'entity': 'InventoryQuantity',
        'count': total,
        'processed': processed,
        'total': total,
        'updated_count': updated_count,
        'skipped_count': skipped_count,
        'failed_count': failed_count,
        'results': results,
    }


def pull_quickbooks_inventory_quantities_to_local(*, limit=None, max_results=None, client=None, task_cache_key=None):
    return import_quickbooks_inventory_quantities(
        limit=limit,
        max_results=max_results,
        client=client,
        task_cache_key=task_cache_key,
    )


def fetch_quickbooks_credit_memos(*, max_results=25, client=None, updated_after=None, page_size=100):
    client = client or QuickBooksAPIClient()
    if updated_after:
        return client.find_updated_since('CreditMemo', updated_after, max_results=max_results, page_size=page_size)
    return client.find_all('CreditMemo', max_results=max_results, order_by='MetaData.LastUpdatedTime', page_size=page_size)


def fetch_quickbooks_bills(*, max_results=25, client=None, updated_after=None, page_size=100):
    client = client or QuickBooksAPIClient()
    if updated_after:
        return client.find_updated_since('Bill', updated_after, max_results=max_results, page_size=page_size)
    return client.find_all('Bill', max_results=max_results, order_by='MetaData.LastUpdatedTime', page_size=page_size)


def fetch_quickbooks_purchase_orders(*, max_results=25, client=None, updated_after=None, page_size=100):
    client = client or QuickBooksAPIClient()
    if updated_after:
        return client.find_updated_since('PurchaseOrder', updated_after, max_results=max_results, page_size=page_size)
    return client.find_all('PurchaseOrder', max_results=max_results, order_by='MetaData.LastUpdatedTime', page_size=page_size)


def _extract_bill_vendor_name(payload):
    return _truncate((payload.get('VendorRef') or {}).get('name') or f"QuickBooks Vendor {payload.get('Id', '')}", limit=255)


def _extract_purchase_order_vendor_name(payload):
    return _truncate((payload.get('VendorRef') or {}).get('name') or f"QuickBooks Vendor {payload.get('Id', '')}", limit=255)


def _match_local_bill_from_quickbooks(payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    doc_number = _normalize_text(payload.get('DocNumber'))
    queryset = CompraProveedor.objects.all()
    if quickbooks_id:
        compra = queryset.filter(quickbooks_id=quickbooks_id).first()
        if compra is not None:
            return compra
    if doc_number:
        compra = queryset.filter(bill_number=doc_number).first()
        if compra is not None:
            return compra
    return None


def _extract_bill_line_specs(payload):
    line_specs = []
    missing_item_refs = []
    missing_amounts = []

    for line in payload.get('Line') or []:
        if (line.get('DetailType') or '') != 'ItemBasedExpenseLineDetail':
            continue
        detail = line.get('ItemBasedExpenseLineDetail') or {}
        item_ref = detail.get('ItemRef') or {}
        quickbooks_item_id = str(item_ref.get('value') or '').strip()
        if not quickbooks_item_id:
            missing_item_refs.append(line.get('Description') or line.get('Id') or 'Bill line')
            continue
        presentacion = Presentacion.objects.select_related('producto').filter(quickbooks_id=quickbooks_item_id).first()
        if presentacion is None:
            missing_item_refs.append(quickbooks_item_id)
            continue

        raw_quantity = detail.get('Qty') or 1
        try:
            quantity = max(int(Decimal(str(raw_quantity or 1))), 1)
        except Exception:
            quantity = 1
        amount = _quantize_money(line.get('Amount') or 0)
        unit_price = detail.get('UnitPrice')
        if unit_price in (None, ''):
            if quantity <= 0:
                missing_amounts.append(quickbooks_item_id)
                continue
            unit_price = amount / Decimal(str(quantity or 1))
        cost = _quantize_money(unit_price)
        line_specs.append({
            'presentacion': presentacion,
            'cantidad': quantity,
            'costo_unitario': cost,
            'descripcion': _truncate(line.get('Description') or presentacion.producto.nombre, limit=255),
        })

    if missing_item_refs:
        raise QuickBooksSyncError(
            _('Bill references QuickBooks items that are not linked locally yet: %(items)s') % {
                'items': ', '.join(str(item) for item in missing_item_refs[:5]),
            }
        )
    if missing_amounts:
        raise QuickBooksSyncError(
            _('Bill contains item lines without enough quantity or amount information to calculate unit cost.')
        )
    if not line_specs:
        raise QuickBooksSyncError(_('QuickBooks Bill does not contain importable item-based expense lines.'))
    return line_specs


def _match_local_purchase_order_from_quickbooks(payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    doc_number = _normalize_text(payload.get('DocNumber'))
    queryset = CompraProveedor.objects.all()
    if quickbooks_id:
        compra = queryset.filter(quickbooks_id=quickbooks_id).first()
        if compra is not None:
            return compra
    if doc_number:
        compra = queryset.filter(bill_number=doc_number).first()
        if compra is not None:
            return compra
    return None


def _extract_purchase_order_line_specs(payload):
    line_specs = []
    missing_item_refs = []

    for line in payload.get('Line') or []:
        if (line.get('DetailType') or '') != 'ItemBasedExpenseLineDetail':
            continue
        detail = line.get('ItemBasedExpenseLineDetail') or {}
        item_ref = detail.get('ItemRef') or {}
        quickbooks_item_id = str(item_ref.get('value') or '').strip()
        if not quickbooks_item_id:
            missing_item_refs.append(line.get('Description') or line.get('Id') or 'Purchase order line')
            continue
        presentacion = Presentacion.objects.select_related('producto').filter(quickbooks_id=quickbooks_item_id).first()
        if presentacion is None:
            missing_item_refs.append(quickbooks_item_id)
            continue

        raw_quantity = detail.get('Qty') or 1
        try:
            quantity = max(int(Decimal(str(raw_quantity or 1))), 1)
        except Exception:
            quantity = 1
        unit_price = detail.get('UnitPrice')
        amount = _quantize_money(line.get('Amount') or 0)
        if unit_price in (None, ''):
            unit_price = amount / Decimal(str(quantity or 1)) if quantity > 0 else Decimal('0.00')
        cost = _quantize_money(unit_price)
        line_specs.append({
            'presentacion': presentacion,
            'cantidad': quantity,
            'costo_unitario': cost,
            'descripcion': _truncate(line.get('Description') or presentacion.producto.nombre, limit=255),
        })

    if missing_item_refs:
        raise QuickBooksSyncError(
            _('Purchase order references QuickBooks items that are not linked locally yet: %(items)s') % {
                'items': ', '.join(str(item) for item in missing_item_refs[:5]),
            }
        )
    if not line_specs:
        raise QuickBooksSyncError(_('QuickBooks Purchase Order does not contain importable item lines.'))
    return line_specs


def _apply_quickbooks_purchase_order_to_local_record(compra, payload):
    update_fields = []
    doc_number = _normalize_text(payload.get('DocNumber'))
    if doc_number and compra.bill_number != doc_number:
        compra.bill_number = doc_number
        update_fields.append('bill_number')

    vendor_name = _extract_purchase_order_vendor_name(payload)
    if vendor_name and compra.proveedor_nombre != vendor_name:
        compra.proveedor_nombre = vendor_name
        update_fields.append('proveedor_nombre')

    txn_date = payload.get('TxnDate')
    parsed_txn_date = _parse_quickbooks_date(txn_date)
    if parsed_txn_date and compra.fecha_compra != parsed_txn_date:
        compra.fecha_compra = parsed_txn_date
        update_fields.append('fecha_compra')

    due_date = payload.get('DueDate')
    parsed_due_date = _parse_quickbooks_date(due_date)
    if compra.fecha_vencimiento != parsed_due_date:
        compra.fecha_vencimiento = parsed_due_date
        update_fields.append('fecha_vencimiento')

    private_note = _truncate(payload.get('PrivateNote') or payload.get('Memo') or '', limit=4000)
    if compra.notas != private_note:
        compra.notas = private_note
        update_fields.append('notas')

    if compra.estado != CompraProveedor.STATUS_SENT:
        compra.estado = CompraProveedor.STATUS_SENT
        update_fields.append('estado')

    compra.quickbooks_id = str(payload.get('Id') or '')
    compra.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    compra.last_synced_at = timezone.now()
    update_fields.extend(['quickbooks_id', 'sync_status', 'last_synced_at'])
    if update_fields:
        compra.save(update_fields=list(dict.fromkeys(update_fields)))
    return compra


@transaction.atomic
def import_quickbooks_purchase_order_record(payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks Purchase Order payload is missing an Id.')

    doc_number = _normalize_text(payload.get('DocNumber'))
    display_name = _extract_purchase_order_vendor_name(payload)
    try:
        line_specs = _extract_purchase_order_line_specs(payload)
    except QuickBooksSyncError as exc:
        _upsert_import_conflict(
            entity_type=QuickBooksImportConflict.ENTITY_PURCHASE_ORDER,
            quickbooks_id=quickbooks_id,
            doc_number=doc_number,
            display_name=display_name,
            reason=str(exc),
            payload=payload,
        )
        return {
            'ok': False,
            'action': 'conflict',
            'entity': 'PurchaseOrder',
            'quickbooks_id': quickbooks_id,
            'label': doc_number or display_name or quickbooks_id,
            'error': str(exc),
        }

    compra = _match_local_purchase_order_from_quickbooks(payload)
    if compra is None:
        compra = CompraProveedor.objects.create(
            proveedor_nombre=display_name,
            bill_number=doc_number,
            fecha_compra=_parse_quickbooks_date(payload.get('TxnDate')) or timezone.localdate(),
            fecha_vencimiento=_parse_quickbooks_date(payload.get('DueDate')),
            notas=_truncate(payload.get('PrivateNote') or payload.get('Memo') or '', limit=4000),
            estado=CompraProveedor.STATUS_SENT,
            quickbooks_id=quickbooks_id,
            sync_status=QUICKBOOKS_SYNC_STATUS_PENDING,
        )
        for line_spec in line_specs:
            line = CompraProveedorLinea(
                compra=compra,
                presentacion=line_spec['presentacion'],
                cantidad=line_spec['cantidad'],
                costo_unitario=line_spec['costo_unitario'],
                descripcion=line_spec['descripcion'],
            )
            line.full_clean()
            line.save()
        compra.recalcular_totales(save=True)
        action = 'created'
    else:
        _apply_quickbooks_purchase_order_to_local_record(compra, payload)
        action = 'updated'

    _apply_quickbooks_purchase_order_to_local_record(compra, payload)
    _resolve_import_conflict(
        entity_type=QuickBooksImportConflict.ENTITY_PURCHASE_ORDER,
        quickbooks_id=quickbooks_id,
        local_model='CompraProveedor',
        local_record_id=compra.id,
    )
    return {
        'ok': True,
        'action': action,
        'entity': 'PurchaseOrder',
        'quickbooks_id': quickbooks_id,
        'local_id': compra.id,
        'label': doc_number or display_name or quickbooks_id,
    }


def _apply_quickbooks_bill_to_local_record(compra, payload):
    update_fields = []
    doc_number = _normalize_text(payload.get('DocNumber'))
    if doc_number and compra.bill_number != doc_number:
        compra.bill_number = doc_number
        update_fields.append('bill_number')

    vendor_name = _extract_bill_vendor_name(payload)
    if vendor_name and compra.proveedor_nombre != vendor_name:
        compra.proveedor_nombre = vendor_name
        update_fields.append('proveedor_nombre')

    txn_date = payload.get('TxnDate')
    parsed_txn_date = _parse_quickbooks_date(txn_date)
    if parsed_txn_date:
        if compra.fecha_compra != parsed_txn_date:
            compra.fecha_compra = parsed_txn_date
            update_fields.append('fecha_compra')

    due_date = payload.get('DueDate')
    parsed_due_date = _parse_quickbooks_date(due_date)
    if compra.fecha_vencimiento != parsed_due_date:
        compra.fecha_vencimiento = parsed_due_date
        update_fields.append('fecha_vencimiento')

    private_note = _truncate(payload.get('PrivateNote') or '', limit=4000)
    if compra.notas != private_note:
        compra.notas = private_note
        update_fields.append('notas')

    compra.quickbooks_id = str(payload.get('Id') or '')
    compra.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    compra.last_synced_at = timezone.now()
    update_fields.extend(['quickbooks_id', 'sync_status', 'last_synced_at'])
    if update_fields:
        compra.save(update_fields=list(dict.fromkeys(update_fields)))
    return compra


@transaction.atomic
def import_quickbooks_bill_record(payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks Bill payload is missing an Id.')

    doc_number = _normalize_text(payload.get('DocNumber'))
    display_name = _extract_bill_vendor_name(payload)
    try:
        line_specs = _extract_bill_line_specs(payload)
    except QuickBooksSyncError as exc:
        _upsert_import_conflict(
            entity_type=QuickBooksImportConflict.ENTITY_BILL,
            quickbooks_id=quickbooks_id,
            doc_number=doc_number,
            display_name=display_name,
            reason=str(exc),
            payload=payload,
        )
        return {
            'ok': False,
            'action': 'conflict',
            'entity': 'Bill',
            'quickbooks_id': quickbooks_id,
            'label': doc_number or display_name or quickbooks_id,
            'error': str(exc),
        }

    compra = _match_local_bill_from_quickbooks(payload)
    if compra is None:
        txn_date = payload.get('TxnDate')
        due_date = payload.get('DueDate')
        compra = CompraProveedor.objects.create(
            proveedor_nombre=display_name,
            bill_number=doc_number,
            fecha_compra=_parse_quickbooks_date(txn_date) or timezone.localdate(),
            fecha_vencimiento=_parse_quickbooks_date(due_date),
            notas=_truncate(payload.get('PrivateNote') or '', limit=4000),
            estado=CompraProveedor.STATUS_RECEIVED,
            quickbooks_id=quickbooks_id,
            sync_status=QUICKBOOKS_SYNC_STATUS_PENDING,
        )
        for line_spec in line_specs:
            line = CompraProveedorLinea(
                compra=compra,
                presentacion=line_spec['presentacion'],
                cantidad=line_spec['cantidad'],
                costo_unitario=line_spec['costo_unitario'],
                descripcion=line_spec['descripcion'],
            )
            line.full_clean()
            line.save()
        compra.recalcular_totales(save=True)
        _apply_supplier_purchase_inventory(compra)
        action = 'created'
    else:
        _apply_quickbooks_bill_to_local_record(compra, payload)
        action = 'updated'

    _apply_quickbooks_bill_to_local_record(compra, payload)
    _resolve_import_conflict(
        entity_type=QuickBooksImportConflict.ENTITY_BILL,
        quickbooks_id=quickbooks_id,
        local_model='CompraProveedor',
        local_record_id=compra.id,
    )
    return {
        'ok': True,
        'action': action,
        'entity': 'Bill',
        'quickbooks_id': quickbooks_id,
        'local_id': compra.id,
        'label': doc_number or display_name or quickbooks_id,
    }


def _match_local_invoice_from_quickbooks(payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    doc_number = _normalize_text(payload.get('DocNumber'))
    if quickbooks_id:
        invoice = Invoice.objects.filter(quickbooks_id=quickbooks_id).first()
        if invoice is not None:
            return invoice, 'Invoice'
        debit_note = NotaAjuste.objects.filter(tipo_documento='DEBITO', quickbooks_id=quickbooks_id).first()
        if debit_note is not None:
            return debit_note, 'NotaAjuste'
    if doc_number:
        invoice = Invoice.objects.filter(numero=doc_number).first()
        if invoice is not None:
            return invoice, 'Invoice'
        debit_note = NotaAjuste.objects.filter(tipo_documento='DEBITO', numero=doc_number).first()
        if debit_note is not None:
            return debit_note, 'NotaAjuste'
    return None, ''


def _match_local_credit_memo_from_quickbooks(payload):
    quickbooks_id = str(payload.get('Id') or '').strip()
    doc_number = _normalize_text(payload.get('DocNumber'))
    queryset = NotaAjuste.objects.filter(tipo_documento='CREDITO')
    if quickbooks_id:
        note = queryset.filter(quickbooks_id=quickbooks_id).first()
        if note is not None:
            return note
    if doc_number:
        note = queryset.filter(numero=doc_number).first()
        if note is not None:
            return note
    return None


def _find_local_customer_from_quickbooks_payload(payload):
    customer_ref = payload.get('CustomerRef') or {}
    customer_quickbooks_id = str(customer_ref.get('value') or '').strip()
    customer_name = _normalize_text(customer_ref.get('name'))
    if customer_quickbooks_id:
        customer = Cliente.objects.select_related('usuario').filter(
            Q(quickbooks_id=customer_quickbooks_id) | Q(usuario__quickbooks_id=customer_quickbooks_id)
        ).first()
        if customer is not None:
            return customer
    if customer_name:
        customer = Cliente.objects.select_related('usuario').filter(
            Q(nombre_empresa__iexact=customer_name)
            | Q(usuario__first_name__iexact=customer_name)
        ).first()
        if customer is not None:
            return customer
        candidates = list(
            Cliente.objects.select_related('usuario').filter(
                Q(nombre_empresa__icontains=customer_name)
                | Q(usuario__first_name__icontains=customer_name)
            ).order_by('id')[:2]
        )
        if len(candidates) == 1:
            return candidates[0]
    return None


def _fetch_quickbooks_customer_payload(*, customer_ref, client=None, customer_cache=None):
    customer_ref = customer_ref or {}
    qb_id = str(customer_ref.get('value') or '').strip()
    cache = customer_cache if customer_cache is not None else {}
    if qb_id and qb_id in cache:
        return cache[qb_id]

    client = client or QuickBooksAPIClient()
    payload = None
    if qb_id:
        try:
            payload = client.read_entity('Customer', qb_id) or client.find_by_id('Customer', qb_id)
        except QuickBooksAPIError:
            payload = None
    if payload is None:
        name = _normalize_text(customer_ref.get('name'))
        if name:
            try:
                payload = client.find_one_by_display_name('Customer', name) or client.find_one_by_name('Customer', name)
            except QuickBooksAPIError:
                payload = None
    if payload and not _quickbooks_customer_payload_is_importable(payload):
        return None
    if qb_id and payload:
        cache[qb_id] = payload
    return payload


def _link_existing_local_customer_from_quickbooks_payload(cliente, qb_customer_payload):
    qb_id = str(qb_customer_payload.get('Id') or '').strip()
    if not qb_id:
        return cliente
    conflict = Cliente.objects.exclude(pk=cliente.pk).filter(quickbooks_id=qb_id).exists()
    if conflict:
        return None
    return _apply_quickbooks_customer_to_local_record(cliente, qb_customer_payload)


def _resolve_local_customer_for_quickbooks_document(payload, *, client=None, customer_cache=None):
    customer_ref = payload.get('CustomerRef') or {}
    if _quickbooks_ref_looks_deleted(customer_ref):
        return None

    customer = _find_local_customer_from_quickbooks_payload(payload)
    if customer is not None:
        return customer

    qb_customer_payload = _fetch_quickbooks_customer_payload(
        customer_ref=customer_ref,
        client=client,
        customer_cache=customer_cache,
    )
    if not qb_customer_payload:
        return None

    company_name = _extract_quickbooks_customer_company_name(qb_customer_payload)
    display_name = _extract_quickbooks_customer_display_name(qb_customer_payload)
    ref_name = _normalize_text(customer_ref.get('name'))
    candidate_names = [name for name in {company_name, display_name, ref_name} if name]

    existing_by_name = None
    for name in candidate_names:
        existing_by_name = Cliente.objects.select_related('usuario').filter(
            Q(nombre_empresa__iexact=name) | Q(usuario__first_name__iexact=name),
        ).filter(Q(quickbooks_id__isnull=True) | Q(quickbooks_id='')).first()
        if existing_by_name is not None:
            break

    if existing_by_name is not None:
        linked = _link_existing_local_customer_from_quickbooks_payload(existing_by_name, qb_customer_payload)
        if linked is not None:
            return linked

    qb_id = str(qb_customer_payload.get('Id') or '').strip()
    if qb_id:
        existing_by_qb = Cliente.objects.select_related('usuario').filter(
            Q(quickbooks_id=qb_id) | Q(usuario__quickbooks_id=qb_id)
        ).first()
        if existing_by_qb is not None:
            return existing_by_qb

    import_result = import_quickbooks_customer_record(qb_customer_payload)
    if import_result.get('ok') and import_result.get('local_id'):
        return Cliente.objects.select_related('usuario').get(pk=import_result['local_id'])

    return _find_local_customer_from_quickbooks_payload({
        **payload,
        'CustomerRef': {
            'value': str(qb_customer_payload.get('Id') or customer_ref.get('value') or '').strip(),
            'name': display_name or company_name or ref_name,
        },
    })


def _build_quickbooks_adjustment_note_number(*, doc_number, prefix, quickbooks_id):
    preferred_number = _normalize_text(doc_number)
    if preferred_number and not NotaAjuste.objects.filter(numero=preferred_number).exists():
        return preferred_number

    base_number = _truncate(f'{prefix}-QB-{quickbooks_id}', limit=30) or f'{prefix}-QB'
    candidate_number = base_number
    suffix = 1
    while NotaAjuste.objects.filter(numero=candidate_number).exists():
        suffix += 1
        suffix_text = f'-{suffix}'
        candidate_number = f'{base_number[:30 - len(suffix_text)]}{suffix_text}'
    return candidate_number


def _create_adjustment_note_from_quickbooks_invoice(payload, *, client=None, customer_cache=None):
    customer = _resolve_local_customer_for_quickbooks_document(payload, client=client, customer_cache=customer_cache)
    if customer is None:
        raise QuickBooksSyncError('No local customer matched this QuickBooks invoice, so a debit note could not be created automatically.')

    quickbooks_id = str(payload.get('Id') or '').strip()
    doc_number = _normalize_text(payload.get('DocNumber'))
    total_amount = _quantize_money(payload.get('TotalAmt') or payload.get('Balance') or 0)
    note = NotaAjuste.objects.create(
        numero=_build_quickbooks_adjustment_note_number(doc_number=doc_number, prefix='DBN', quickbooks_id=quickbooks_id),
        cliente=customer,
        tipo_documento='DEBITO',
        tipo_ajuste='FINANCIERO',
        estado='APROBADA',
        motivo='OTHER',
        descripcion=_truncate(payload.get('PrivateNote') or _('Imported from QuickBooks invoice'), limit=4000),
        monto=total_amount,
        total=total_amount,
        impacto_saldo=total_amount,
        inventario_estado='NO_APLICA',
        quickbooks_id=quickbooks_id,
        sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
        last_synced_at=timezone.now(),
        aprobada_en=timezone.now(),
    )
    return note


def _parse_single_quickbooks_sales_line(line):
    detail_type = line.get('DetailType') or ''
    if detail_type != 'SalesItemLineDetail':
        return None
    detail = line.get('SalesItemLineDetail') or {}
    item_ref = detail.get('ItemRef') or {}
    qb_item_id = str(item_ref.get('value') or '').strip()
    description = _normalize_text(line.get('Description') or item_ref.get('name'))
    qty = max(int(float(detail.get('Qty') or 1)), 1)
    unit_price = _quantize_money(
        detail.get('UnitPrice') or (Decimal(str(line.get('Amount') or 0)) / Decimal(str(qty or 1)))
    )
    subtotal = _quantize_money(line.get('Amount') or (unit_price * Decimal(str(qty))))
    presentacion = None
    if qb_item_id:
        presentacion = Presentacion.objects.select_related('producto').filter(quickbooks_id=qb_item_id).first()
    product_name = presentacion.producto.nombre if presentacion else (description or item_ref.get('name') or 'QuickBooks item')
    presentation_name = presentacion.nombre if presentacion else (description or product_name)
    return {
        'presentacion': presentacion,
        'producto_nombre': _truncate(product_name, limit=255),
        'presentacion_nombre': _truncate(presentation_name, limit=120),
        'cantidad': qty,
        'precio': unit_price,
        'subtotal': subtotal,
    }


def _parse_quickbooks_sales_line_specs(payload):
    specs = []
    for line in payload.get('Line') or []:
        detail_type = line.get('DetailType') or ''
        if detail_type in {'SubTotalLineDetail', 'DiscountLineDetail'}:
            continue
        if detail_type == 'GroupLineDetail':
            for sub_line in line.get('GroupLineDetail', {}).get('Line', []) or []:
                parsed = _parse_single_quickbooks_sales_line(sub_line)
                if parsed:
                    specs.append(parsed)
            continue
        parsed = _parse_single_quickbooks_sales_line(line)
        if parsed:
            specs.append(parsed)
    return specs


@transaction.atomic
def _create_invoice_from_quickbooks_invoice(payload, *, client=None, customer_cache=None):
    customer = _resolve_local_customer_for_quickbooks_document(payload, client=client, customer_cache=customer_cache)
    if customer is None:
        raise QuickBooksSyncError(
            'No local customer matched this QuickBooks invoice, so a local invoice could not be created automatically.'
        )

    quickbooks_id = str(payload.get('Id') or '').strip()
    doc_number = _normalize_text(payload.get('DocNumber'))
    total_amount = _quantize_money(payload.get('TotalAmt') or payload.get('Balance') or 0)
    balance = _quantize_money(
        payload.get('Balance') if payload.get('Balance') not in (None, '') else total_amount
    )
    line_specs = _parse_quickbooks_sales_line_specs(payload)
    if not line_specs:
        line_specs = [{
            'presentacion': None,
            'producto_nombre': str(_('QuickBooks imported total')),
            'presentacion_nombre': str(_('Summary')),
            'cantidad': 1,
            'precio': total_amount,
            'subtotal': total_amount,
        }]

    pedido = Pedido.objects.create(
        cliente=customer,
        origen='BACKOFFICE',
        canal_toma='QUICKBOOKS_IMPORT',
        estado='INVOICE_GENERADA',
        nota_cliente=_truncate(payload.get('PrivateNote') or _('Imported from QuickBooks'), limit=4000),
        total=total_amount,
    )

    pedido_items = []
    for spec in line_specs:
        if spec.get('presentacion') is None:
            continue
        pedido_items.append(PedidoItem(
            pedido=pedido,
            presentacion=spec['presentacion'],
            cantidad_solicitada=spec['cantidad'],
            cantidad=spec['cantidad'],
            precio=spec['precio'],
            subtotal=spec['subtotal'],
        ))
    if pedido_items:
        PedidoItem.objects.bulk_create(pedido_items)

    invoice = Invoice.objects.create(
        pedido=pedido,
        cliente=customer,
        metodo_entrega='LTG',
        driver=None,
        subtotal=total_amount,
        total_neto=total_amount,
        saldo_cliente=balance,
        quickbooks_id=quickbooks_id,
        sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
        last_synced_at=timezone.now(),
        fecha_documento=_parse_quickbooks_date(payload.get('TxnDate')) or timezone.localdate(),
    )
    status_fields = _apply_quickbooks_invoice_status_to_local_record(invoice, payload, client=client)
    if status_fields:
        invoice.save(update_fields=status_fields)
    if doc_number and not Invoice.objects.exclude(pk=invoice.pk).filter(numero=doc_number).exists():
        invoice.numero = doc_number
        invoice.save(update_fields=['numero'])

    invoice_items = []
    for spec in line_specs:
        pedido_item = None
        if spec.get('presentacion') is not None:
            pedido_item = PedidoItem.objects.filter(pedido=pedido, presentacion=spec['presentacion']).first()
        invoice_items.append(InvoiceItem(
            invoice=invoice,
            pedido_item=pedido_item,
            presentacion=spec.get('presentacion'),
            producto_nombre=spec['producto_nombre'],
            presentacion_nombre=spec['presentacion_nombre'],
            cantidad_facturada=spec['cantidad'],
            precio_unitario=spec['precio'],
            precio_venta_sugerido_unitario=spec['precio'],
            subtotal=spec['subtotal'],
        ))
    InvoiceItem.objects.bulk_create(invoice_items)
    return invoice


def _create_adjustment_note_from_quickbooks_credit_memo(payload, *, client=None, customer_cache=None):
    customer = _resolve_local_customer_for_quickbooks_document(payload, client=client, customer_cache=customer_cache)
    if customer is None:
        raise QuickBooksSyncError('No local customer matched this QuickBooks credit memo, so a credit note could not be created automatically.')

    quickbooks_id = str(payload.get('Id') or '').strip()
    doc_number = _normalize_text(payload.get('DocNumber'))
    total_amount = _quantize_money(payload.get('TotalAmt') or payload.get('Balance') or 0)
    note = NotaAjuste.objects.create(
        numero=_build_quickbooks_adjustment_note_number(doc_number=doc_number, prefix='CRN', quickbooks_id=quickbooks_id),
        cliente=customer,
        tipo_documento='CREDITO',
        tipo_ajuste='FINANCIERO',
        estado='APROBADA',
        motivo='OTHER',
        tipo_credito='CREDIT_DUMP',
        descripcion=_truncate(payload.get('PrivateNote') or _('Imported from QuickBooks credit memo'), limit=4000),
        monto=total_amount,
        total=total_amount,
        impacto_saldo=total_amount,
        inventario_estado='NO_APLICA',
        quickbooks_id=quickbooks_id,
        sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
        last_synced_at=timezone.now(),
        aprobada_en=timezone.now(),
    )
    return note


def _assign_unique_document_number(record, doc_number):
    doc_number = _normalize_text(doc_number)
    if not doc_number or getattr(record, 'numero', '') == doc_number:
        return []
    model = record.__class__
    if model.objects.exclude(pk=record.pk).filter(numero=doc_number).exists():
        return []
    record.numero = doc_number
    return ['numero']


def _apply_quickbooks_invoice_to_local_record(record, payload, *, client=None):
    update_fields = []
    update_fields.extend(_assign_unique_document_number(record, payload.get('DocNumber')))
    total_amount = _quantize_money(payload.get('TotalAmt') or payload.get('Balance') or 0)
    raw_balance = payload.get('Balance')
    balance = _quantize_money(raw_balance if raw_balance not in (None, '') else total_amount)
    if isinstance(record, Invoice):
        if record.subtotal != total_amount:
            record.subtotal = total_amount
            update_fields.append('subtotal')
        if record.total_neto != total_amount:
            record.total_neto = total_amount
            update_fields.append('total_neto')
        if record.saldo_cliente != balance:
            record.saldo_cliente = balance
            update_fields.append('saldo_cliente')
        txn_date = _parse_quickbooks_date(payload.get('TxnDate'))
        if txn_date and record.fecha_documento != txn_date:
            record.fecha_documento = txn_date
            update_fields.append('fecha_documento')
        update_fields.extend(_apply_quickbooks_invoice_status_to_local_record(record, payload, client=client))
    else:
        if record.total != total_amount:
            record.total = total_amount
            update_fields.append('total')
        if record.monto != total_amount:
            record.monto = total_amount
            update_fields.append('monto')
        if record.impacto_saldo != total_amount:
            record.impacto_saldo = total_amount
            update_fields.append('impacto_saldo')
    record.quickbooks_id = str(payload.get('Id') or '')
    record.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    record.last_synced_at = timezone.now()
    update_fields.extend(['quickbooks_id', 'sync_status', 'last_synced_at'])
    if update_fields:
        record.save(update_fields=list(dict.fromkeys(update_fields)))
    return record


def _apply_quickbooks_credit_memo_to_local_record(note, payload):
    update_fields = []
    update_fields.extend(_assign_unique_document_number(note, payload.get('DocNumber')))
    total_amount = _quantize_money(payload.get('TotalAmt') or payload.get('Balance') or 0)
    if note.total != total_amount:
        note.total = total_amount
        update_fields.append('total')
    if note.monto != total_amount:
        note.monto = total_amount
        update_fields.append('monto')
    if note.impacto_saldo != total_amount:
        note.impacto_saldo = total_amount
        update_fields.append('impacto_saldo')
    note.quickbooks_id = str(payload.get('Id') or '')
    note.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
    note.last_synced_at = timezone.now()
    update_fields.extend(['quickbooks_id', 'sync_status', 'last_synced_at'])
    if update_fields:
        note.save(update_fields=list(dict.fromkeys(update_fields)))
    return note


def import_quickbooks_invoice_record(payload, *, client=None, customer_cache=None):
    client = client or QuickBooksAPIClient()
    quickbooks_id = str(payload.get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks invoice payload is missing an Id.')
    doc_number = _normalize_text(payload.get('DocNumber'))
    display_name = _truncate((payload.get('CustomerRef') or {}).get('name') or doc_number or quickbooks_id, limit=255)
    if not _quickbooks_accounting_document_is_importable(payload):
        return _skip_import_result(
            entity='Invoice',
            quickbooks_id=quickbooks_id,
            label=doc_number or display_name or quickbooks_id,
            reason='QuickBooks invoice references an inactive or deleted customer.',
        )
    record, local_model = _match_local_invoice_from_quickbooks(payload)
    if record is None:
        try:
            record = _create_invoice_from_quickbooks_invoice(payload, client=client, customer_cache=customer_cache)
        except QuickBooksSyncError as exc:
            _upsert_import_conflict(
                entity_type=QuickBooksImportConflict.ENTITY_INVOICE,
                quickbooks_id=quickbooks_id,
                doc_number=doc_number,
                display_name=display_name,
                reason=str(exc),
                payload=payload,
            )
            return {
                'ok': False,
                'action': 'conflict',
                'entity': 'Invoice',
                'quickbooks_id': quickbooks_id,
                'label': doc_number or display_name or quickbooks_id,
                'error': str(exc),
            }
        local_model = 'Invoice'
        action = 'created'
    else:
        _apply_quickbooks_invoice_to_local_record(record, payload, client=client)
        action = 'matched'

    _apply_quickbooks_invoice_to_local_record(record, payload, client=client)
    _resolve_import_conflict(
        entity_type=QuickBooksImportConflict.ENTITY_INVOICE,
        quickbooks_id=quickbooks_id,
        local_model=local_model,
        local_record_id=record.id,
    )
    txn_date = _parse_quickbooks_date(payload.get('TxnDate'))
    due_date = _parse_quickbooks_date(payload.get('DueDate'))
    invoice_balance = _quantize_money(payload.get('Balance') if payload.get('Balance') not in (None, '') else payload.get('TotalAmt') or 0)
    return {
        'ok': True,
        'action': action,
        'entity': 'Invoice',
        'quickbooks_id': quickbooks_id,
        'local_id': record.id,
        'label': doc_number or getattr(record, 'numero', '') or quickbooks_id,
        'doc_number': doc_number,
        'txn_date': txn_date.isoformat() if txn_date else '',
        'due_date': due_date.isoformat() if due_date else '',
        'balance': str(invoice_balance),
        'fecha_documento': getattr(record, 'fecha_documento', None).isoformat() if getattr(record, 'fecha_documento', None) else '',
    }


def import_quickbooks_credit_memo_record(payload, *, client=None, customer_cache=None):
    client = client or QuickBooksAPIClient()
    quickbooks_id = str(payload.get('Id') or '').strip()
    if not quickbooks_id:
        raise QuickBooksSyncError('QuickBooks credit memo payload is missing an Id.')
    doc_number = _normalize_text(payload.get('DocNumber'))
    display_name = _truncate((payload.get('CustomerRef') or {}).get('name') or doc_number or quickbooks_id, limit=255)
    if not _quickbooks_accounting_document_is_importable(payload):
        return _skip_import_result(
            entity='CreditMemo',
            quickbooks_id=quickbooks_id,
            label=doc_number or display_name or quickbooks_id,
            reason='QuickBooks credit memo references an inactive or deleted customer.',
        )
    note = _match_local_credit_memo_from_quickbooks(payload)
    if note is None:
        try:
            note = _create_adjustment_note_from_quickbooks_credit_memo(payload, client=client, customer_cache=customer_cache)
        except QuickBooksSyncError as exc:
            _upsert_import_conflict(
                entity_type=QuickBooksImportConflict.ENTITY_CREDIT_MEMO,
                quickbooks_id=quickbooks_id,
                doc_number=doc_number,
                display_name=display_name,
                reason=str(exc),
                payload=payload,
            )
            return {
                'ok': False,
                'action': 'conflict',
                'entity': 'CreditMemo',
                'quickbooks_id': quickbooks_id,
                'label': doc_number or display_name or quickbooks_id,
                'error': str(exc),
            }
        action = 'created'
    else:
        _apply_quickbooks_credit_memo_to_local_record(note, payload)
        action = 'matched'

    _apply_quickbooks_credit_memo_to_local_record(note, payload)
    _resolve_import_conflict(
        entity_type=QuickBooksImportConflict.ENTITY_CREDIT_MEMO,
        quickbooks_id=quickbooks_id,
        local_model='NotaAjuste',
        local_record_id=note.id,
    )
    return {
        'ok': True,
        'action': action,
        'entity': 'CreditMemo',
        'quickbooks_id': quickbooks_id,
        'local_id': note.id,
        'label': doc_number or note.numero or quickbooks_id,
    }


def quickbooks_accounting_import_enabled():
    return getattr(settings, 'QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS', False)


def _empty_accounting_documents_import_result():
    return {
        'entity': 'AccountingDocument',
        'count': 0,
        'matched_count': 0,
        'created_count': 0,
        'updated_count': 0,
        'skipped_count': 0,
        'conflict_count': 0,
        'invoice_result': {'count': 0, 'latest_updated_at': None},
        'credit_memo_result': {'count': 0, 'latest_updated_at': None},
        'results': [],
        'disabled': True,
    }


def import_quickbooks_accounting_documents(*, max_results=25, client=None, invoice_updated_after=None, credit_memo_updated_after=None):
    if not quickbooks_accounting_import_enabled():
        return _empty_accounting_documents_import_result()
    client = client or QuickBooksAPIClient()
    page_size = _quickbooks_catalog_page_size()
    invoice_records = fetch_quickbooks_invoices(
        max_results=max_results,
        client=client,
        updated_after=invoice_updated_after,
        page_size=page_size,
    )
    credit_memo_records = fetch_quickbooks_credit_memos(
        max_results=max_results,
        client=client,
        updated_after=credit_memo_updated_after,
        page_size=page_size,
    )
    customer_cache = {}
    results = [
        import_quickbooks_invoice_record(record, client=client, customer_cache=customer_cache)
        for record in invoice_records
    ]
    results.extend(
        import_quickbooks_credit_memo_record(record, client=client, customer_cache=customer_cache)
        for record in credit_memo_records
    )
    return {
        'entity': 'AccountingDocument',
        'count': len(results),
        'matched_count': sum(1 for item in results if item.get('ok') and item.get('action') != 'skipped'),
        'created_count': sum(1 for item in results if item.get('ok') and item.get('action') == 'created'),
        'updated_count': sum(1 for item in results if item.get('ok') and item.get('action') == 'matched'),
        'skipped_count': sum(1 for item in results if item.get('action') == 'skipped'),
        'conflict_count': sum(1 for item in results if item.get('action') == 'conflict'),
        'invoice_result': {
            'count': len(invoice_records),
            'latest_updated_at': _serialize_cursor(_latest_payload_update(invoice_records)),
        },
        'credit_memo_result': {
            'count': len(credit_memo_records),
            'latest_updated_at': _serialize_cursor(_latest_payload_update(credit_memo_records)),
        },
        'results': results,
    }


def pull_quickbooks_accounting_documents_to_local(*, max_results=None, client=None, force_full=False, task_cache_key=None):
    """Pull QuickBooks invoices and credit memos into local Invoice and NotaAjuste records."""
    if not quickbooks_accounting_import_enabled():
        raise QuickBooksSyncError(
            'QuickBooks invoice import is disabled. Invoices are only exported from this app to QuickBooks.'
        )
    client = client or QuickBooksAPIClient()
    connection = client.connection
    run_started_at = timezone.now()
    serialized_run_started_at = _serialize_cursor(run_started_at)

    invoice_cursor = None if force_full else _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('invoice')))
    credit_memo_cursor = None if force_full else _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('credit_memo')))

    if task_cache_key:
        cache.set(task_cache_key, {'status': 'running', 'progress': 5, 'operation': 'AccountingDocument'}, timeout=60 * 60)

    result = import_quickbooks_accounting_documents(
        max_results=max_results,
        client=client,
        invoice_updated_after=invoice_cursor,
        credit_memo_updated_after=credit_memo_cursor,
    )

    connection.set_sync_cursor(
        _sync_cursor_key('invoice'),
        result.get('invoice_result', {}).get('latest_updated_at') or serialized_run_started_at,
    )
    connection.set_sync_cursor(
        _sync_cursor_key('credit_memo'),
        result.get('credit_memo_result', {}).get('latest_updated_at') or serialized_run_started_at,
    )
    connection.save(update_fields=['sync_state', 'updated_at'])

    result['incremental'] = not force_full
    result['run_started_at'] = serialized_run_started_at
    if task_cache_key:
        cache.set(
            task_cache_key,
            {'status': 'completed', 'progress': 100, 'operation': 'AccountingDocument', 'result': result},
            timeout=60 * 60,
        )
    return result


def import_quickbooks_invoices(*, max_results=25, client=None, updated_after=None, task_cache_key=None):
    """Import QuickBooks invoices into local Invoice records with dates, balances, and status."""
    if not quickbooks_accounting_import_enabled():
        raise QuickBooksSyncError(
            'QuickBooks invoice import is disabled. Invoices are only exported from this app to QuickBooks.'
        )
    client = client or QuickBooksAPIClient()
    page_size = _quickbooks_catalog_page_size()
    customer_cache = {}

    def _import_invoice(record):
        return import_quickbooks_invoice_record(record, client=client, customer_cache=customer_cache)

    summary = _empty_import_batch_summary(entity_name='Invoice')
    processed_offset = 0
    progress_total = None

    for page_records, total_count in iter_quickbooks_invoice_pages(
        max_results=max_results,
        client=client,
        updated_after=updated_after,
        page_size=page_size,
    ):
        if progress_total is None:
            progress_total = total_count or len(page_records)
        page_result = _import_batch_result(
            entity_name='Invoice',
            records=page_records,
            import_callable=_import_invoice,
            task_cache_key=task_cache_key,
            client=client,
            progress_offset=processed_offset,
            progress_total=progress_total,
        )
        summary = _merge_import_batch_summaries(summary, page_result)
        processed_offset += len(page_records)

    return summary


def pull_quickbooks_invoices_to_local(*, max_results=None, client=None, force_full=False, task_cache_key=None):
    """Pull QuickBooks invoices into local Invoice records (invoice date, due date, open balance)."""
    if not quickbooks_accounting_import_enabled():
        raise QuickBooksSyncError(
            'QuickBooks invoice import is disabled. Invoices are only exported from this app to QuickBooks.'
        )
    client = client or QuickBooksAPIClient()
    connection = client.connection
    run_started_at = timezone.now()
    serialized_run_started_at = _serialize_cursor(run_started_at)
    invoice_cursor = None if force_full else _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('invoice')))

    if task_cache_key:
        cache.set(
            task_cache_key,
            {'status': 'running', 'progress': 5, 'operation': 'import_invoices_to_local'},
            timeout=60 * 60,
        )

    result = import_quickbooks_invoices(
        max_results=max_results,
        client=client,
        updated_after=invoice_cursor,
        task_cache_key=task_cache_key,
    )

    latest_updated_at = result.get('latest_updated_at')
    if latest_updated_at:
        connection.set_sync_cursor(_sync_cursor_key('invoice'), latest_updated_at)
    elif int(result.get('count') or 0) > 0:
        connection.set_sync_cursor(_sync_cursor_key('invoice'), serialized_run_started_at)
    connection.save(update_fields=['sync_state', 'updated_at'])

    result['incremental'] = not force_full
    result['run_started_at'] = serialized_run_started_at
    return result


def import_quickbooks_bills(*, max_results=25, client=None, updated_after=None, task_cache_key=None):
    records = fetch_quickbooks_bills(max_results=max_results, client=client, updated_after=updated_after)
    return _import_batch_result(entity_name='Bill', records=records, import_callable=import_quickbooks_bill_record, task_cache_key=task_cache_key)


def import_quickbooks_purchase_orders(*, max_results=25, client=None, updated_after=None, task_cache_key=None):
    records = fetch_quickbooks_purchase_orders(max_results=max_results, client=client, updated_after=updated_after)
    return _import_batch_result(entity_name='PurchaseOrder', records=records, import_callable=import_quickbooks_purchase_order_record, task_cache_key=task_cache_key)


def _sync_cursor_key(entity_name):
    return f'quickbooks:{entity_name.lower()}'


def _products_missing_quickbooks_images_queryset():
    linked_product_ids = (
        Presentacion.objects.filter(quickbooks_id__isnull=False)
        .exclude(quickbooks_id='')
        .values_list('producto_id', flat=True)
        .distinct()
    )
    return (
        Producto.objects.filter(
            Q(quickbooks_id__isnull=False) & ~Q(quickbooks_id='')
            | Q(pk__in=linked_product_ids)
        )
        .filter(Q(imagen__isnull=True) | Q(imagen=''))
        .distinct()
        .order_by('nombre', 'id')
    )


def _resolve_product_quickbooks_item_id(producto):
    qb_id = str(producto.quickbooks_id or '').strip()
    if qb_id:
        return qb_id
    return str(
        Presentacion.objects.filter(producto=producto)
        .exclude(quickbooks_id__isnull=True)
        .exclude(quickbooks_id='')
        .order_by('id')
        .values_list('quickbooks_id', flat=True)
        .first() or ''
    ).strip()


def _local_catalog_has_quickbooks_linked_items():
    return (
        Presentacion.objects.filter(quickbooks_id__isnull=False)
        .exclude(quickbooks_id='')
        .exists()
    )


def _resolve_item_import_force_full(force_full=False):
    if force_full:
        return True
    return not _local_catalog_has_quickbooks_linked_items()


def pull_quickbooks_items_to_local(*, max_results=25, client=None, force_full=False, task_cache_key=None, skip_images=None):
    client = client or QuickBooksAPIClient()
    connection = client.connection
    run_started_at = timezone.now()

    force_full = _resolve_item_import_force_full(force_full)
    item_cursor = None if force_full else _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('item')))
    items = import_quickbooks_items(
        max_results=max_results,
        client=client,
        updated_after=item_cursor,
        task_cache_key=task_cache_key,
        skip_images=skip_images,
    )

    connection.set_sync_cursor(_sync_cursor_key('item'), items.get('latest_updated_at') or _serialize_cursor(run_started_at))
    connection.save(update_fields=['sync_state', 'updated_at'])

    return {
        'items': items,
        'run_started_at': _serialize_cursor(run_started_at),
        'incremental': not force_full,
        'force_full': force_full,
    }


def refresh_linked_quickbooks_items(*, limit=None, max_results=None, client=None, task_cache_key=None, skip_images=None):
    """Refresh linked catalog rows and import any new/changed QuickBooks items since the last cursor."""
    if limit is None:
        limit = max_results
    client = client or QuickBooksAPIClient()
    connection = client.connection
    if skip_images is None:
        skip_images = _catalog_sync_skip_images()

    item_cursor = _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('item')))
    incremental_result = import_quickbooks_items(
        max_results=limit,
        client=client,
        updated_after=item_cursor,
        task_cache_key=task_cache_key,
        skip_images=skip_images,
    )

    queryset = _linked_catalog_presentacion_queryset()
    if limit is not None:
        queryset = queryset[:max(int(limit), 0)]

    linked_presentaciones = list(queryset)
    qb_ids = _linked_catalog_quickbooks_ids(linked_presentaciones)
    items_map = _fetch_quickbooks_items_map(client=client, wanted_ids=qb_ids)
    prefetched_presentaciones = {}
    for presentacion in linked_presentaciones:
        for raw_id in (presentacion.quickbooks_id, getattr(presentacion.producto, 'quickbooks_id', None)):
            qb_id = str(raw_id or '').strip()
            if qb_id:
                prefetched_presentaciones.setdefault(qb_id, presentacion)
    lookup_cache = _build_catalog_lookup_cache()
    payloads = []
    for qb_id in qb_ids:
        payload = items_map.get(qb_id)
        if not payload:
            payload = _fetch_quickbooks_item_payload(item_id=qb_id, client=client)
        if not payload:
            payloads.append({'Id': qb_id, '_missing_in_qb': True})
            continue
        payloads.append(payload)

    def _import_payload(payload):
        qb_id = str(payload.get('Id') or '').strip()
        if payload.get('_missing_in_qb'):
            fetched_payload = _fetch_quickbooks_item_payload(item_id=qb_id, client=client)
            if fetched_payload:
                return import_quickbooks_item_record(
                    fetched_payload,
                    client=client,
                    skip_enrich=True,
                    skip_images=skip_images,
                    prefetched_presentacion=prefetched_presentaciones.get(qb_id),
                    lookup_cache=lookup_cache,
                    force_active_refresh=True,
                )
            presentacion = _deactivate_local_product_for_quickbooks_item(
                quickbooks_id=qb_id,
                prefetched_presentacion=prefetched_presentaciones.get(qb_id),
            )
            if presentacion is not None:
                producto = presentacion.producto
                return {
                    'ok': True,
                    'action': 'deactivated',
                    'entity': 'Item',
                    'quickbooks_id': qb_id,
                    'local_id': presentacion.id,
                    'label': f'{producto.nombre} / {presentacion.nombre}',
                }
            return {
                'ok': False,
                'action': 'missing',
                'entity': 'Item',
                'quickbooks_id': qb_id,
                'label': qb_id,
                'error': 'Item not found in QuickBooks.',
            }
        return import_quickbooks_item_record(
            payload,
            client=client,
            skip_enrich=True,
            skip_images=skip_images,
            prefetched_presentacion=prefetched_presentaciones.get(qb_id),
            lookup_cache=lookup_cache,
            force_active_refresh=True,
        )

    linked_result = _import_batch_result(
        entity_name='LinkedItem',
        records=payloads,
        import_callable=_import_payload,
        task_cache_key=task_cache_key,
        client=client,
    )
    cache.delete('catalogo:productos_activos_v2')

    latest_updated_at = incremental_result.get('latest_updated_at') or linked_result.get('latest_updated_at')
    if latest_updated_at:
        connection.set_sync_cursor(_sync_cursor_key('item'), latest_updated_at)
        connection.save(update_fields=['sync_state', 'updated_at'])

    result = _merge_import_batch_results(incremental_result, linked_result)
    result['linked_count'] = len(qb_ids)
    result['incremental'] = True
    result['incremental_count'] = int(incremental_result.get('count') or 0)
    return result


def _advance_sync_cursor(connection, *, key, batch_result, run_started_at):
    latest_updated_at = (batch_result or {}).get('latest_updated_at')
    if latest_updated_at:
        connection.set_sync_cursor(key, latest_updated_at)
        return
    if int((batch_result or {}).get('count') or 0) <= 0:
        return
    connection.set_sync_cursor(key, _serialize_cursor(run_started_at))


def pull_quickbooks_to_local(*, max_results=25, client=None, force_full=False, task_cache_key=None, skip_images=None):
    client = client or QuickBooksAPIClient()
    connection = client.connection
    run_started_at = timezone.now()
    if skip_images is None:
        skip_images = bool(getattr(settings, 'QUICKBOOKS_CATALOG_SYNC_SKIP_IMAGES', True))

    customer_cursor = None if force_full else _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('customer')))
    item_cursor = None if force_full else _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('item')))
    invoice_cursor = None if force_full else _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('invoice')))
    credit_memo_cursor = None if force_full else _cursor_for_query(connection.get_sync_cursor(_sync_cursor_key('credit_memo')))

    customers = import_quickbooks_customers(
        max_results=max_results,
        client=client,
        updated_after=customer_cursor,
        task_cache_key=task_cache_key,
    )
    items = import_quickbooks_items(
        max_results=max_results,
        client=client,
        updated_after=item_cursor,
        task_cache_key=task_cache_key,
        skip_images=skip_images,
    )
    if quickbooks_accounting_import_enabled():
        accounting_documents = import_quickbooks_accounting_documents(
            max_results=max_results,
            client=client,
            invoice_updated_after=invoice_cursor,
            credit_memo_updated_after=credit_memo_cursor,
        )
    else:
        accounting_documents = _empty_accounting_documents_import_result()

    serialized_run_started_at = _serialize_cursor(run_started_at)
    _advance_sync_cursor(connection, key=_sync_cursor_key('customer'), batch_result=customers, run_started_at=run_started_at)
    _advance_sync_cursor(connection, key=_sync_cursor_key('item'), batch_result=items, run_started_at=run_started_at)
    if quickbooks_accounting_import_enabled():
        _advance_sync_cursor(
            connection,
            key=_sync_cursor_key('invoice'),
            batch_result=accounting_documents.get('invoice_result'),
            run_started_at=run_started_at,
        )
        _advance_sync_cursor(
            connection,
            key=_sync_cursor_key('credit_memo'),
            batch_result=accounting_documents.get('credit_memo_result'),
            run_started_at=run_started_at,
        )
    connection.save(update_fields=['sync_state', 'updated_at'])

    return {
        'customers': customers,
        'items': items,
        'accounting_documents': accounting_documents,
        'run_started_at': serialized_run_started_at,
        'incremental': not force_full,
    }


def retry_quickbooks_import_conflict(conflict, *, user=None):
    payload = conflict.payload or {}
    try:
        if conflict.entity_type == QuickBooksImportConflict.ENTITY_CUSTOMER:
            return import_quickbooks_customer_record(payload)
        if conflict.entity_type == QuickBooksImportConflict.ENTITY_VENDOR:
            return import_quickbooks_vendor_record(payload)
        if conflict.entity_type == QuickBooksImportConflict.ENTITY_ITEM:
            return import_quickbooks_item_record(payload)
        if conflict.entity_type == QuickBooksImportConflict.ENTITY_INVOICE:
            return import_quickbooks_invoice_record(payload)
        if conflict.entity_type == QuickBooksImportConflict.ENTITY_CREDIT_MEMO:
            return import_quickbooks_credit_memo_record(payload)
        if conflict.entity_type == QuickBooksImportConflict.ENTITY_BILL:
            return import_quickbooks_bill_record(payload)
        if conflict.entity_type == QuickBooksImportConflict.ENTITY_PURCHASE_ORDER:
            return import_quickbooks_purchase_order_record(payload)
    except Exception as exc:
        # Don't let DB errors or integrity problems raise a 500 from the retry endpoint.
        # Return a structured failure result so the caller can display it.
        return {
            'ok': False,
            'action': 'failed',
            'entity': conflict.get_entity_type_display() or conflict.entity_type,
            'quickbooks_id': str(payload.get('Id') or conflict.quickbooks_id or ''),
            'label': payload.get('DisplayName') or payload.get('Name') or conflict.display_name or '',
            'error': str(exc),
        }
    raise QuickBooksSyncError('Unsupported QuickBooks conflict entity.')


def link_quickbooks_import_conflict(conflict, *, local_record_id=None, local_model=None, user=None, resolution_note=''):
    payload = conflict.payload or {}
    local_record_id = int(local_record_id or conflict.local_record_id or 0)
    local_model = (local_model or conflict.local_model or '').strip() or None
    if local_record_id <= 0:
        raise QuickBooksSyncError('Provide a valid local record ID to link this QuickBooks record.')

    if conflict.entity_type == QuickBooksImportConflict.ENTITY_CUSTOMER:
        record = Cliente.objects.select_related('usuario').get(pk=local_record_id)
        _apply_quickbooks_customer_to_local_record(record, payload)
        local_model = 'Cliente'
    elif conflict.entity_type == QuickBooksImportConflict.ENTITY_VENDOR:
        record = Proveedor.objects.get(pk=local_record_id)
        defaults = _build_vendor_import_defaults(payload)
        for field, value in defaults.items():
            setattr(record, field, value)
        record.save(update_fields=[*defaults.keys(), 'actualizado_en'])
        _mark_vendor_imported(record, quickbooks_id=payload.get('Id'))
        local_model = 'Proveedor'
    elif conflict.entity_type == QuickBooksImportConflict.ENTITY_ITEM:
        record = Presentacion.objects.select_related('producto').get(pk=local_record_id)
        _apply_quickbooks_item_to_local_record(record, payload)
        local_model = 'Presentacion'
    elif conflict.entity_type == QuickBooksImportConflict.ENTITY_INVOICE:
        if local_model == 'NotaAjuste':
            record = NotaAjuste.objects.get(pk=local_record_id, tipo_documento='DEBITO')
        else:
            record = Invoice.objects.get(pk=local_record_id)
            local_model = 'Invoice'
        _apply_quickbooks_invoice_to_local_record(record, payload)
    elif conflict.entity_type == QuickBooksImportConflict.ENTITY_CREDIT_MEMO:
        record = NotaAjuste.objects.get(pk=local_record_id, tipo_documento='CREDITO')
        _apply_quickbooks_credit_memo_to_local_record(record, payload)
        local_model = 'NotaAjuste'
    elif conflict.entity_type == QuickBooksImportConflict.ENTITY_BILL:
        record = CompraProveedor.objects.get(pk=local_record_id)
        _apply_quickbooks_bill_to_local_record(record, payload)
        local_model = 'CompraProveedor'
    elif conflict.entity_type == QuickBooksImportConflict.ENTITY_PURCHASE_ORDER:
        record = CompraProveedor.objects.get(pk=local_record_id)
        _apply_quickbooks_purchase_order_to_local_record(record, payload)
        local_model = 'CompraProveedor'
    else:
        raise QuickBooksSyncError('Unsupported QuickBooks conflict entity.')

    _resolve_import_conflict(
        entity_type=conflict.entity_type,
        quickbooks_id=payload.get('Id') or conflict.quickbooks_id,
        local_model=local_model,
        local_record_id=record.id,
        user=user,
        resolution_note=resolution_note,
    )
    return {
        'ok': True,
        'action': 'linked',
        'entity': conflict.entity_type,
        'quickbooks_id': str(payload.get('Id') or conflict.quickbooks_id),
        'local_id': record.id,
        'local_model': local_model,
    }


def _normalize_inventory_start_date_if_needed(
    presentacion,
    existing,
    *,
    client,
    sync_qty_on_hand=True,
    sync_pricing=True,
):
    if not sync_qty_on_hand:
        return existing

    if not _quickbooks_item_is_inventory(existing):
        return existing

    inv_start = _coerce_local_date(existing.get('InvStartDate'))
    target_start = _get_inventory_start_date()
    if inv_start and inv_start <= target_start:
        return existing

    desired_payload = _build_item_payload(
        presentacion,
        client=client,
        income_account_ref=existing.get('IncomeAccountRef') or None,
        remote_payload=existing,
        sync_qty_on_hand=sync_qty_on_hand,
        sync_pricing=sync_pricing,
    )
    desired_payload['InvStartDate'] = target_start.isoformat()
    recreated = _recreate_presentacion_as_inventory_item(
        presentacion,
        existing,
        desired_payload,
        client=client,
        sync_qty_on_hand=sync_qty_on_hand,
    )
    _mark_synced(presentacion, recreated.get('Id'))
    return recreated


def _push_presentacion_to_quickbooks_item(
    presentacion,
    existing,
    *,
    client,
    sync_qty_on_hand=False,
    sync_pricing=False,
):
    existing_income_ref = existing.get('IncomeAccountRef') if _quickbooks_item_is_inventory(existing) else None
    desired_payload = _build_item_payload(
        presentacion,
        client=client,
        income_account_ref=existing_income_ref or None,
        remote_payload=existing,
        sync_qty_on_hand=sync_qty_on_hand,
        sync_pricing=sync_pricing,
    )
    if not _item_payload_needs_update(
        existing,
        desired_payload,
        sync_qty_on_hand=sync_qty_on_hand,
        sync_pricing=sync_pricing,
    ):
        normalized = _normalize_inventory_start_date_if_needed(
            presentacion,
            existing,
            client=client,
            sync_qty_on_hand=sync_qty_on_hand,
            sync_pricing=sync_pricing,
        )
        if normalized is not existing:
            return _sync_result(entity='Item', action='converted', payload=normalized)
        _mark_synced(presentacion, existing.get('Id'))
        return _sync_result(entity='Item', action='existing', payload=existing)

    if _needs_inventory_type_conversion(existing, desired_payload):
        updated = _convert_linked_item_to_inventory(
            presentacion,
            existing,
            desired_payload,
            client=client,
            sync_qty_on_hand=sync_qty_on_hand,
        )
        _mark_synced(presentacion, updated.get('Id'))
        return _sync_result(entity='Item', action='converted', payload=updated)

    updated = client.update_item(_build_sparse_update_payload(existing, desired_payload))
    _mark_synced(presentacion, updated.get('Id'))
    return _sync_result(entity='Item', action='updated', payload=updated)


def sync_product(*, presentacion, client=None, sync_qty_on_hand=False, sync_pricing=False):
    client = client or QuickBooksAPIClient()
    try:
        if presentacion.quickbooks_id:
            existing = client.find_by_id('Item', presentacion.quickbooks_id)
            if existing:
                return _push_presentacion_to_quickbooks_item(
                    presentacion,
                    existing,
                    client=client,
                    sync_qty_on_hand=sync_qty_on_hand,
                    sync_pricing=sync_pricing,
                )

        item_name = _build_item_name(presentacion)
        existing = client.find_one_by_name('Item', item_name)
        if existing:
            result = _push_presentacion_to_quickbooks_item(
                presentacion,
                existing,
                client=client,
                sync_qty_on_hand=sync_qty_on_hand,
                sync_pricing=sync_pricing,
            )
            if result.get('action') == 'existing':
                result = {**result, 'action': 'linked'}
            return result

        created = client.create_item(_build_item_payload(presentacion, client=client))
        _mark_synced(presentacion, created.get('Id'))
        return _sync_result(entity='Item', action='created', payload=created)
    except (QuickBooksAPIError, QuickBooksSyncError) as exc:
        _mark_failed(presentacion)
        raise QuickBooksSyncError(_summarize_quickbooks_error(exc)) from exc


def push_linked_quickbooks_items(*, limit=None, client=None, task_cache_key=None):
    """Push local catalog metadata for rows already linked in QuickBooks.

    Updates name/SKU/description/active (and related item metadata) only.
    Price, cost, and QtyOnHand stay under QuickBooks control.
    """
    del client, task_cache_key
    queryset = _linked_catalog_presentacion_queryset().select_related('stock_operativo')
    if limit is not None:
        queryset = queryset[:max(int(limit), 0)]

    record_ids = list(queryset.values_list('id', flat=True))
    if not record_ids:
        return {
            'linked_count': 0,
            'updated_count': 0,
            'unchanged_count': 0,
            'success_count': 0,
            'failed_count': 0,
            'results': [],
        }

    result = sync_product_batch_by_ids(record_ids)
    updated_count = 0
    unchanged_count = 0
    for item in result.get('results', []):
        if not item.get('ok'):
            continue
        action = (item.get('result') or {}).get('action')
        if action in {'updated', 'converted'}:
            updated_count += 1
        elif action in {'existing', 'linked'}:
            unchanged_count += 1

    result['linked_count'] = len(record_ids)
    result['updated_count'] = updated_count
    result['unchanged_count'] = unchanged_count
    return result


def _ensure_adjustment_item(client):
    adjustment_name = 'LTG Adjustment Item'
    existing = client.find_one_by_name('Item', adjustment_name)
    if existing:
        return existing
    return client.create_item({
        'Name': adjustment_name,
        'Type': 'NonInventory',
        'Active': True,
        'Description': 'Generic adjustment item for La Tortilla Grocery sync.',
        'UnitPrice': 0,
        'IncomeAccountRef': _get_default_income_account_ref(client),
    })


def _build_sales_line(*, item_ref, amount, description, quantity=1, unit_price=None):
    quantity = 1 if quantity is None else int(quantity)
    amount_decimal = _quantize_money(amount or 0)
    if unit_price is not None:
        unit_price_decimal = _quantize_money(unit_price)
    elif quantity > 0:
        unit_price_decimal = _quantize_money(amount_decimal / Decimal(str(quantity)))
    else:
        unit_price_decimal = Decimal('0.00')
    amount_decimal = _quantize_money(unit_price_decimal * Decimal(str(quantity)))
    return {
        'DetailType': 'SalesItemLineDetail',
        'Amount': _as_float(amount_decimal),
        'Description': _truncate(description, limit=4000),
        'SalesItemLineDetail': {
            'ItemRef': item_ref,
            'Qty': quantity,
            'UnitPrice': _as_float(unit_price_decimal),
        },
    }


LOCAL_PAYMENT_TERM_QB_NAMES = {
    'PREPAY': ('Prepay', 'Due on receipt', 'Due upon receipt'),
    'COD': ('COD', 'Due on receipt', 'Due upon receipt'),
    'NET7': ('Net 7', 'NET7', 'Net7'),
    'ACH_NET7': ('ACH Net 7', 'ACH NET 7', 'ACH_NET7'),
    'NET14': ('Net 14', 'NET14', 'Net14'),
    'NET21': ('Net 21', 'NET21', 'Net21'),
}

LOCAL_PAYMENT_TERM_DUE_DAYS = {
    'PREPAY': 0,
    'COD': 0,
    'NET7': 7,
    'ACH_NET7': 7,
    'NET14': 14,
    'NET21': 21,
}


def _preferred_term_name_for_local(terminos_pago):
    code = _normalize_text(terminos_pago).upper()
    names = LOCAL_PAYMENT_TERM_QB_NAMES.get(code) or ()
    return names[0] if names else ''


def _find_term_by_names(client, names):
    for name in names:
        cleaned = _normalize_text(name)
        if not cleaned:
            continue
        escaped = client._escape_query_value(cleaned)
        terms = client.query(
            f"select Id, Name, DueDays from Term where Name = '{escaped}' maxresults 1"
        ).get('Term', [])
        if terms:
            return terms[0]
    return None


def _resolve_or_create_sales_term_ref(client, terminos_pago):
    code = _normalize_text(terminos_pago).upper()
    if not code:
        return None
    names = LOCAL_PAYMENT_TERM_QB_NAMES.get(code)
    if not names:
        return None
    term_cache = getattr(client, '_ltg_sales_term_cache', None)
    if term_cache is None:
        term_cache = {}
        setattr(client, '_ltg_sales_term_cache', term_cache)
    if code in term_cache:
        return term_cache[code]
    try:
        term = _find_term_by_names(client, names)
        if term is None:
            due_days = LOCAL_PAYMENT_TERM_DUE_DAYS.get(code)
            if due_days is None:
                term_cache[code] = None
                return None
            term = client.create_entity('Term', {
                'Name': names[0],
                'DueDays': int(due_days),
            })
        term_id = str(term.get('Id') or '').strip()
        if not term_id:
            term_cache[code] = None
            return None
        ref = {'value': term_id, 'name': term.get('Name') or names[0]}
        term_cache[code] = ref
        return ref
    except (QuickBooksAPIError, QuickBooksSyncError, TypeError, ValueError, RuntimeError) as exc:
        logger.warning('Could not resolve QuickBooks sales term for %s: %s', code, exc)
        term_cache[code] = None
        return None


def _build_invoice_payment_terms_payload(*, invoice, client):
    """Return optional DueDate / SalesTermRef for QB Invoice create. Never raises."""
    payload = {}
    try:
        from config.facturacion.services import resolve_invoice_payment_due_date

        due_date = resolve_invoice_payment_due_date(invoice)
        if due_date:
            payload['DueDate'] = due_date.isoformat()
    except Exception as exc:  # noqa: BLE001 - keep invoice export resilient
        logger.warning('Could not resolve invoice due date for %s: %s', getattr(invoice, 'numero', invoice.pk), exc)

    terminos = getattr(getattr(invoice, 'cliente', None), 'terminos_pago', '') or ''
    try:
        term_ref = _resolve_or_create_sales_term_ref(client, terminos)
        if term_ref:
            payload['SalesTermRef'] = term_ref
    except Exception as exc:  # noqa: BLE001 - keep invoice export resilient
        logger.warning(
            'Could not attach QuickBooks sales term for invoice %s: %s',
            getattr(invoice, 'numero', getattr(invoice, 'pk', '')),
            exc,
        )
    return payload


def _client_account_ref_cache(client):
    cache = getattr(client, '_ltg_account_refs', None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            client._ltg_account_refs = cache
        except Exception:
            return {}
    return cache


def _get_undeposited_funds_account_ref(client):
    cache = _client_account_ref_cache(client)
    if 'undeposited' in cache:
        return cache['undeposited']
    account_ref = _account_ref_from_setting(client, 'QUICKBOOKS_UNDEPOSITED_FUNDS_ACCOUNT_ID')
    if not account_ref:
        account_ref = _first_account_ref_from_queries(
            client,
            (
                "select Id, Name from Account where AccountSubType = 'UndepositedFunds' maxresults 1",
                "select Id, Name from Account where Name = 'Undeposited Funds' maxresults 1",
            ),
        )
    cache['undeposited'] = account_ref
    return account_ref


def _get_cash_account_ref(client):
    cache = _client_account_ref_cache(client)
    if 'cash' in cache:
        return cache['cash']
    account_ref = _account_ref_from_setting(client, 'QUICKBOOKS_CASH_ACCOUNT_ID')
    if not account_ref:
        account_ref = _first_account_ref_from_queries(
            client,
            (
                "select Id, Name from Account where AccountSubType = 'CashOnHand' maxresults 1",
                "select Id, Name from Account where Name = 'Cash' maxresults 1",
                "select Id, Name from Account where Name = 'Cash on hand' maxresults 1",
            ),
        )
    cache['cash'] = account_ref
    return account_ref


def _is_cash_payment_method(method):
    return str(method or '').strip().upper() == 'CASH'


def _delivery_payment_rows(delivery):
    payments = getattr(delivery, 'payments', None)
    if payments is None:
        return []
    try:
        return list(payments.all())
    except (TypeError, AttributeError):
        try:
            return list(payments)
        except TypeError:
            return []


def _payment_slices_for_delivery(delivery):
    """
    Split a paid delivery into cash vs non-cash slices for QuickBooks deposit routing.

    Cash goes to the Cash account. Card/ACH/cheque/transfer/Zelle/etc. go to
    Undeposited Funds (Payments to Deposit) for later bank reconciliation.
    """
    if delivery is None:
        return []

    payment_rows = _delivery_payment_rows(delivery)
    if payment_rows:
        slices = []
        for row in payment_rows:
            amount = _quantize_money(getattr(row, 'monto', 0) or 0)
            if amount <= 0:
                continue
            method = str(getattr(row, 'metodo_pago', '') or '').strip().upper()
            if hasattr(row, 'get_metodo_pago_display'):
                method_label = str(row.get_metodo_pago_display() or method)
            else:
                method_label = method
            slices.append(
                {
                    'amount': amount,
                    'is_cash': _is_cash_payment_method(method),
                    'method': method,
                    'method_label': method_label,
                }
            )
        if slices:
            return slices

    method = str(getattr(delivery, 'metodo_pago', '') or '').strip().upper()
    total = _quantize_money(getattr(delivery, 'monto_pagado', 0) or 0)
    if total <= 0:
        return []

    if method == 'MIXTO':
        cash_amount = _quantize_money(getattr(delivery, 'monto_pagado_cash', 0) or 0)
        cheque_amount = _quantize_money(getattr(delivery, 'monto_pagado_cheque', 0) or 0)
        slices = []
        if cash_amount > 0:
            slices.append(
                {
                    'amount': cash_amount,
                    'is_cash': True,
                    'method': 'CASH',
                    'method_label': 'Cash',
                }
            )
        if cheque_amount > 0:
            slices.append(
                {
                    'amount': cheque_amount,
                    'is_cash': False,
                    'method': 'CHEQUE',
                    'method_label': 'Cheque',
                }
            )
        if slices:
            return slices

    if hasattr(delivery, 'get_metodo_pago_display') and method:
        method_label = str(delivery.get_metodo_pago_display() or method)
    else:
        method_label = method

    # MULTIPLE without line items cannot be safely split: route to Undeposited Funds.
    is_cash = _is_cash_payment_method(method)
    return [
        {
            'amount': total,
            'is_cash': is_cash,
            'method': method,
            'method_label': method_label,
        }
    ]


def _deposit_account_ref_for_payment_slice(client, *, is_cash):
    if is_cash:
        cash_ref = _get_cash_account_ref(client)
        if not cash_ref:
            raise QuickBooksSyncError(
                'QuickBooks Cash account was not found. '
                'Set QUICKBOOKS_CASH_ACCOUNT_ID or create a Cash / Cash on hand account in QuickBooks.'
            )
        return cash_ref
    undeposited_ref = _get_undeposited_funds_account_ref(client)
    if not undeposited_ref:
        raise QuickBooksSyncError(
            'QuickBooks Undeposited Funds account was not found. '
            'Set QUICKBOOKS_UNDEPOSITED_FUNDS_ACCOUNT_ID or ensure Undeposited Funds exists in QuickBooks.'
        )
    return undeposited_ref


def _local_invoice_paid_amount(invoice):
    delivery = getattr(invoice, 'delivery', None)
    if delivery is None:
        return Decimal('0.00')
    if getattr(delivery, 'estado_pago', '') != 'PAGADO':
        return Decimal('0.00')
    return _quantize_money(getattr(delivery, 'monto_pagado', 0) or 0)


def _remote_invoice_open_balance(remote_invoice):
    if remote_invoice is None:
        return Decimal('0.00')
    if remote_invoice.get('Balance') is not None:
        return _quantize_money(remote_invoice.get('Balance'))
    if remote_invoice.get('TotalAmt') is not None:
        return _quantize_money(remote_invoice.get('TotalAmt'))
    return Decimal('0.00')


def _payment_txn_date_for_invoice(invoice):
    delivery = getattr(invoice, 'delivery', None)
    if delivery is not None and getattr(delivery, 'delivered_at', None):
        return timezone.localtime(delivery.delivered_at).date()
    if getattr(invoice, 'creada_en', None):
        return timezone.localtime(invoice.creada_en).date()
    return timezone.localdate()


def _build_invoice_payment_payload(
    *,
    customer_qb_id,
    invoice_qb_id,
    invoice,
    amount,
    deposit_ref,
    method_label='',
):
    note = f'La Tortilla delivery payment for {invoice.numero}'
    if method_label:
        note = f'{note} | Method: {method_label}'
    payload = {
        'CustomerRef': {'value': customer_qb_id},
        'TotalAmt': _as_float(amount),
        'TxnDate': _payment_txn_date_for_invoice(invoice).isoformat(),
        'PrivateNote': _truncate(note, limit=4000),
        'Line': [
            {
                'Amount': _as_float(amount),
                'LinkedTxn': [
                    {
                        'TxnId': invoice_qb_id,
                        'TxnType': 'Invoice',
                    }
                ],
            }
        ],
        'DepositToAccountRef': deposit_ref,
    }
    return payload


def _sync_invoice_payment_if_needed(*, client, invoice, remote_invoice, customer_quickbooks_id):
    """
    Create QB Payment(s) when the local delivery is paid.

    Cash → DepositToAccountRef = Cash account (funds already received).
    Non-cash (card, ACH, cheque, transfer, Zelle, etc.) → Undeposited Funds
    (Payments to Deposit) so bank reconciliation can match the deposit later.

    Soft-fails: never raises; returns a result dict or None.
    """
    paid_amount = _local_invoice_paid_amount(invoice)
    if paid_amount <= 0:
        return None

    invoice_qb_id = str((remote_invoice or {}).get('Id') or getattr(invoice, 'quickbooks_id', '') or '').strip()
    customer_qb_id = str(customer_quickbooks_id or '').strip()
    if not invoice_qb_id or not customer_qb_id:
        return {'action': 'skipped', 'error': 'Missing QuickBooks invoice or customer id for payment.'}

    open_balance = _remote_invoice_open_balance(remote_invoice)
    if open_balance <= 0:
        return {'action': 'skipped', 'reason': 'Invoice already has zero open balance in QuickBooks.'}

    delivery = getattr(invoice, 'delivery', None)
    slices = _payment_slices_for_delivery(delivery)
    if not slices:
        slices = [
            {
                'amount': paid_amount,
                'is_cash': False,
                'method': '',
                'method_label': '',
            }
        ]

    remaining = min(paid_amount, open_balance)
    created_payments = []
    try:
        for payment_slice in slices:
            if remaining <= 0:
                break
            slice_amount = min(_quantize_money(payment_slice['amount']), remaining)
            if slice_amount <= 0:
                continue
            deposit_ref = _deposit_account_ref_for_payment_slice(
                client,
                is_cash=bool(payment_slice.get('is_cash')),
            )
            payment_payload = _build_invoice_payment_payload(
                customer_qb_id=customer_qb_id,
                invoice_qb_id=invoice_qb_id,
                invoice=invoice,
                amount=slice_amount,
                deposit_ref=deposit_ref,
                method_label=payment_slice.get('method_label') or '',
            )
            created_payment = client.create_payment(payment_payload)
            created_payments.append(
                {
                    'quickbooks_id': str(created_payment.get('Id') or ''),
                    'amount': _as_float(slice_amount),
                    'is_cash': bool(payment_slice.get('is_cash')),
                    'method': payment_slice.get('method') or '',
                    'deposit_account': deposit_ref,
                    'payload': created_payment,
                }
            )
            remaining = _quantize_money(remaining - slice_amount)
    except (QuickBooksAPIError, QuickBooksSyncError, TypeError, ValueError, AttributeError) as exc:
        logger.warning(
            'QuickBooks payment sync failed for invoice %s: %s',
            getattr(invoice, 'numero', invoice.pk),
            exc,
        )
        return {'action': 'failed', 'error': str(exc), 'payments': created_payments}

    if not created_payments:
        return {'action': 'skipped', 'reason': 'No payment amount remaining to sync.'}

    update_fields = []
    if invoice.qb_payment_status != 'PAID':
        invoice.qb_payment_status = 'PAID'
        update_fields.append('qb_payment_status')
    if update_fields:
        invoice.save(update_fields=update_fields)

    total_synced = sum((_quantize_money(item['amount']) for item in created_payments), Decimal('0.00'))
    return {
        'action': 'created',
        'quickbooks_id': created_payments[0]['quickbooks_id'],
        'amount': _as_float(total_synced),
        'payments': created_payments,
        'payload': created_payments[0]['payload'],
    }


def _apply_local_due_date_from_payload(invoice, terms_payload):
    due_raw = (terms_payload or {}).get('DueDate')
    if not due_raw:
        return
    due_date = parse_date(str(due_raw))
    if due_date and invoice.qb_due_date != due_date:
        invoice.qb_due_date = due_date
        invoice.save(update_fields=['qb_due_date'])


def _build_adjustment_item_ref(client):
    item = _ensure_adjustment_item(client)
    return {'value': str(item.get('Id')), 'name': item.get('Name', '')}


def _find_transaction_by_doc_number(client, entity_name, doc_number):
    escaped = _normalize_text(doc_number).replace("'", "\\'")
    response = client.query(f"select * from {entity_name} where DocNumber = '{escaped}' maxresults 1")
    entities = response.get(entity_name, [])
    return entities[0] if entities else None


def fetch_quickbooks_invoices(*, max_results=25, client=None, updated_after=None, page_size=100):
    client = client or QuickBooksAPIClient()
    if updated_after:
        return client.find_updated_since('Invoice', updated_after, max_results=max_results, page_size=page_size)
    return client.find_all('Invoice', max_results=max_results, order_by='MetaData.LastUpdatedTime', page_size=page_size)


def iter_quickbooks_invoice_pages(*, max_results=None, client=None, updated_after=None, page_size=100):
    """Yield QuickBooks invoice pages so large imports can be processed incrementally."""
    client = client or QuickBooksAPIClient()
    page_size = max(int(page_size or 100), 1)
    where_clause = None
    if updated_after:
        where_clause = f"MetaData.LastUpdatedTime > '{client._escape_query_value(updated_after)}'"

    start_position = 1
    remaining = None if max_results is None else max(int(max_results), 0)
    total_count = None

    while True:
        batch_size = page_size if remaining is None else min(page_size, remaining)
        response = client.query(
            client._build_select_statement(
                'Invoice',
                where_clause=where_clause,
                order_by='MetaData.LastUpdatedTime',
                start_position=start_position,
                max_results=batch_size,
            )
        )
        if total_count is None:
            total_count = int(response.get('totalCount') or 0)
        batch = response.get('Invoice', []) or []
        if not batch:
            break
        yield batch, total_count
        if remaining is not None:
            remaining -= len(batch)
            if remaining <= 0:
                break
        if len(batch) < batch_size:
            break
        start_position += len(batch)


def _quickbooks_invoice_fetch_chunk_size():
    configured = getattr(settings, 'QUICKBOOKS_INVOICE_STATUS_FETCH_CHUNK_SIZE', 50)
    return min(max(int(configured or 50), 1), 100)


def _fetch_quickbooks_invoice_id_chunk(*, client, invoice_ids):
    chunk = [str(invoice_id).strip() for invoice_id in invoice_ids if str(invoice_id or '').strip()]
    if not chunk:
        return []
    if len(chunk) == 1:
        record = client.find_by_id('Invoice', chunk[0])
        return [record] if record else []

    in_list = ', '.join(f"'{client._escape_query_value(invoice_id)}'" for invoice_id in chunk)
    response = client.query(
        client._build_select_statement(
            'Invoice',
            where_clause=f'Id IN ({in_list})',
            max_results=len(chunk),
        )
    )
    batch = response.get('Invoice', [])
    if isinstance(batch, dict):
        return [batch]
    return batch or []


def _fetch_quickbooks_invoices_by_ids(*, client, invoice_ids, chunk_size=None):
    found = {}
    ids = [str(invoice_id).strip() for invoice_id in invoice_ids if str(invoice_id or '').strip()]
    if not ids:
        return found

    chunk_size = chunk_size or _quickbooks_invoice_fetch_chunk_size()
    chunk_size = min(max(int(chunk_size), 1), 100)
    for offset in range(0, len(ids), chunk_size):
        chunk = ids[offset:offset + chunk_size]
        try:
            batch = _fetch_quickbooks_invoice_id_chunk(client=client, invoice_ids=chunk)
        except QuickBooksAPIError as exc:
            error_text = str(exc).lower()
            if len(chunk) <= 1 or ('400' not in error_text and 'bad request' not in error_text):
                raise
            midpoint = len(chunk) // 2
            found.update(_fetch_quickbooks_invoices_by_ids(client=client, invoice_ids=chunk[:midpoint], chunk_size=midpoint))
            found.update(_fetch_quickbooks_invoices_by_ids(client=client, invoice_ids=chunk[midpoint:], chunk_size=len(chunk) - midpoint))
            continue
        for record in batch:
            invoice_id = str(record.get('Id') or '').strip()
            if invoice_id:
                found[invoice_id] = record
    return found


_QB_STATUS_REFRESH_PAYMENT_STATUSES = frozenset({'', 'OPEN', 'DUE', 'DUE_TODAY', 'OVERDUE'})
_QB_STATUS_REFRESH_EMAIL_STATUSES = frozenset({'', 'NOT_SET', 'NEED_TO_SEND'})
_QB_STATUS_REFRESH_BULK_UPDATE_SIZE = 500


def _invoice_queryset_for_qb_status_refresh(*, force_all=False):
    queryset = (
        Invoice.objects.filter(quickbooks_id__isnull=False)
        .exclude(quickbooks_id='')
        .only('id', 'numero', 'quickbooks_id', 'qb_payment_status', 'qb_due_date', 'qb_email_status')
        .order_by('id')
    )
    if force_all:
        return queryset
    return queryset.filter(
        Q(qb_payment_status__in=_QB_STATUS_REFRESH_PAYMENT_STATUSES)
        | Q(qb_email_status__in=_QB_STATUS_REFRESH_EMAIL_STATUSES)
    )


def _flush_qb_status_refresh_updates(invoices):
    if not invoices:
        return 0
    Invoice.objects.bulk_update(
        invoices,
        ['qb_payment_status', 'qb_due_date', 'qb_email_status'],
        batch_size=_QB_STATUS_REFRESH_BULK_UPDATE_SIZE,
    )
    return len(invoices)


def refresh_linked_quickbooks_invoice_status(*, limit=None, max_results=None, client=None, task_cache_key=None, force_all=False):
    """Re-fetch QuickBooks payment status for invoices already linked locally."""
    if limit is None:
        limit = max_results
    client = client or QuickBooksAPIClient()
    queryset = _invoice_queryset_for_qb_status_refresh(force_all=force_all)
    if limit is not None:
        queryset = queryset[:max(int(limit), 0)]

    local_invoices = list(queryset)
    local_by_qb_id = {
        str(invoice.quickbooks_id).strip(): invoice
        for invoice in local_invoices
        if str(invoice.quickbooks_id or '').strip()
    }
    if not local_by_qb_id:
        return {
            'entity': 'LinkedInvoiceStatus',
            'count': 0,
            'updated_count': 0,
            'skipped_count': 0,
            'missing_count': 0,
            'linked_count': 0,
            'incremental': not force_all,
        }

    qb_ids = sorted(local_by_qb_id.keys())
    invoices_map = _fetch_quickbooks_invoices_by_ids(client=client, invoice_ids=qb_ids)
    results = []
    pending_updates = []
    processed_count = 0
    changed_count = 0
    skipped_count = 0
    missing_count = 0

    if task_cache_key:
        cache.set(task_cache_key, {'status': 'running', 'progress': 0, 'operation': 'LinkedInvoiceStatus'}, timeout=60 * 60)

    total = len(qb_ids)
    for index, qb_id in enumerate(qb_ids, start=1):
        invoice = local_by_qb_id[qb_id]
        payload = invoices_map.get(qb_id)
        if not payload:
            missing_count += 1
            results.append({
                'ok': False,
                'action': 'missing',
                'entity': 'Invoice',
                'quickbooks_id': qb_id,
                'label': invoice.numero or qb_id,
                'error': 'Invoice not found in QuickBooks.',
            })
            continue
        if not _quickbooks_accounting_document_is_importable(payload):
            skipped_count += 1
            results.append(_skip_import_result(
                entity='Invoice',
                quickbooks_id=qb_id,
                label=invoice.numero or qb_id,
                reason='QuickBooks invoice references an inactive or deleted customer.',
            ))
            continue

        payment_status, due_date, email_status = _derive_quickbooks_invoice_status(
            payload,
            client=client,
            skip_deposited_lookup=True,
        )
        changed = (
            invoice.qb_payment_status != payment_status
            or invoice.qb_due_date != due_date
            or invoice.qb_email_status != email_status
        )
        if changed:
            invoice.qb_payment_status = payment_status
            invoice.qb_due_date = due_date
            invoice.qb_email_status = email_status
            pending_updates.append(invoice)
            changed_count += 1

        processed_count += 1
        results.append({
            'ok': True,
            'action': 'matched',
            'entity': 'Invoice',
            'quickbooks_id': qb_id,
            'local_id': invoice.id,
            'label': invoice.numero or qb_id,
        })

        if len(pending_updates) >= _QB_STATUS_REFRESH_BULK_UPDATE_SIZE:
            _flush_qb_status_refresh_updates(pending_updates)
            pending_updates = []

        if task_cache_key and (index == total or index % 100 == 0):
            pct = int((index / total) * 90) + 5
            cache.set(
                task_cache_key,
                {'status': 'running', 'progress': min(pct, 95), 'operation': 'LinkedInvoiceStatus', 'result': {'processed': index, 'total': total}},
                timeout=60 * 60,
            )

    _flush_qb_status_refresh_updates(pending_updates)

    summary = {
        'entity': 'LinkedInvoiceStatus',
        'count': len(results),
        'updated_count': processed_count,
        'changed_count': changed_count,
        'skipped_count': skipped_count,
        'missing_count': missing_count,
        'linked_count': len(qb_ids),
        'incremental': not force_all,
        'results': results,
    }
    if task_cache_key:
        cache.set(task_cache_key, {'status': 'completed', 'progress': 100, 'operation': 'LinkedInvoiceStatus', 'result': summary}, timeout=60 * 60)
    return summary


def sync_invoice(*, invoice, client=None):
    client = client or QuickBooksAPIClient()
    try:
        if invoice.quickbooks_id:
            existing = client.find_by_id('Invoice', invoice.quickbooks_id)
            if existing:
                _mark_synced(invoice, existing.get('Id'))
                customer_qb_id = str(
                    ((existing.get('CustomerRef') or {}).get('value'))
                    or getattr(invoice.cliente, 'quickbooks_id', '')
                    or ''
                )
                payment_result = _sync_invoice_payment_if_needed(
                    client=client,
                    invoice=invoice,
                    remote_invoice=existing,
                    customer_quickbooks_id=customer_qb_id,
                )
                return _sync_result(
                    entity='Invoice',
                    action='existing',
                    payload=existing,
                    payment=payment_result,
                )

        customer_result = sync_customer(cliente=invoice.cliente, client=client)
        txn_date = timezone.localtime(invoice.creada_en).date() if invoice.creada_en else timezone.localdate()
        lines = []
        adjustment_item_ref = None
        inventory_presentaciones = []
        for item in invoice.items.select_related('presentacion__producto').all():
            quantity = int(item.cantidad_facturada or 0)
            if quantity <= 0:
                continue
            if item.presentacion_id:
                product_result = sync_product(
                    presentacion=item.presentacion,
                    client=client,
                    sync_qty_on_hand=False,
                )
                _prepare_inventory_item_for_txn_date(
                    client=client,
                    presentacion=item.presentacion,
                    txn_date=txn_date,
                    sync_qty_on_hand=False,
                )
                item.presentacion.refresh_from_db(fields=['quickbooks_id'])
                inventory_presentaciones.append(item.presentacion)
                item_ref = _build_item_ref(
                    item.presentacion,
                    remote_payload=product_result.get('payload'),
                )
            else:
                adjustment_item_ref = adjustment_item_ref or _build_adjustment_item_ref(client)
                item_ref = adjustment_item_ref
            lines.append(_build_sales_line(
                item_ref=item_ref,
                amount=item.subtotal,
                description=f'{item.producto_nombre} - {item.presentacion_nombre}',
                quantity=quantity,
                unit_price=item.precio_unitario,
            ))

        if not lines:
            raise QuickBooksSyncError(_('Invoice has no billable line items to send to QuickBooks.'))

        existing = _find_transaction_by_doc_number(client, 'Invoice', invoice.numero)
        if existing:
            _mark_synced(invoice, existing.get('Id'))
            payment_result = _sync_invoice_payment_if_needed(
                client=client,
                invoice=invoice,
                remote_invoice=existing,
                customer_quickbooks_id=customer_result['quickbooks_id'],
            )
            return _sync_result(
                entity='Invoice',
                action='linked',
                payload=existing,
                payment=payment_result,
            )

        resolved_txn_date = _resolve_txn_date_for_inventory_items(
            client=client,
            presentaciones=inventory_presentaciones,
            txn_date=txn_date,
        )
        private_note = _truncate(f'La Tortilla invoice {invoice.numero}', limit=4000)
        if resolved_txn_date != txn_date:
            private_note = _truncate(
                f'{private_note} | Original ERP date: {txn_date.isoformat()}',
                limit=4000,
            )
        terms_payload = _build_invoice_payment_terms_payload(invoice=invoice, client=client)
        invoice_payload = {
            'CustomerRef': {'value': customer_result['quickbooks_id']},
            'DocNumber': invoice.numero,
            'TxnDate': resolved_txn_date.isoformat(),
            'PrivateNote': private_note,
            'Line': lines,
        }
        invoice_payload.update(terms_payload)
        created = client.create_invoice(invoice_payload)
        _mark_synced(invoice, created.get('Id'))
        _apply_local_due_date_from_payload(invoice, terms_payload)
        payment_result = _sync_invoice_payment_if_needed(
            client=client,
            invoice=invoice,
            remote_invoice=created,
            customer_quickbooks_id=customer_result['quickbooks_id'],
        )
        return _sync_result(
            entity='Invoice',
            action='created',
            payload=created,
            payment=payment_result,
            terms=terms_payload or None,
        )
    except (QuickBooksAPIError, QuickBooksSyncError) as exc:
        _mark_failed(invoice)
        raise QuickBooksSyncError(str(exc)) from exc


def _build_adjustment_lines(note, client, *, txn_date=None):
    adjustment_item_ref = None
    lines = []
    presentaciones = []
    for item in note.items.select_related('presentacion__producto').all():
        amount = _quantize_money(item.total or Decimal(str(item.monto_unitario or '0')) * Decimal(str(item.cantidad or 1)))
        if item.presentacion_id:
            product_result = sync_product(
                presentacion=item.presentacion,
                client=client,
                sync_qty_on_hand=False,
            )
            if txn_date is not None:
                _prepare_inventory_item_for_txn_date(
                    client=client,
                    presentacion=item.presentacion,
                    txn_date=txn_date,
                    sync_qty_on_hand=False,
                )
                item.presentacion.refresh_from_db(fields=['quickbooks_id'])
            presentaciones.append(item.presentacion)
            item_ref = _build_item_ref(
                item.presentacion,
                remote_payload=product_result.get('payload'),
            )
        else:
            adjustment_item_ref = adjustment_item_ref or _build_adjustment_item_ref(client)
            item_ref = adjustment_item_ref
        lines.append(_build_sales_line(
            item_ref=item_ref,
            amount=amount,
            description=item.descripcion,
            quantity=item.cantidad,
            unit_price=item.monto_unitario,
        ))
    if lines:
        return lines, presentaciones

    adjustment_item_ref = _build_adjustment_item_ref(client)
    amount = _quantize_money(note.total or note.monto or note.impacto_saldo)
    if amount <= 0:
        amount = Decimal('0.01')
    return [
        _build_sales_line(
            item_ref=adjustment_item_ref,
            amount=amount,
            description=note.descripcion or note.get_motivo_display(),
            quantity=1,
            unit_price=amount,
        )
    ], []


def sync_adjustment_note(*, note, client=None):
    client = client or QuickBooksAPIClient()
    if note.estado != 'APROBADA':
        raise QuickBooksSyncError('Only approved adjustment notes can be synced to QuickBooks.')
    try:
        if note.quickbooks_id:
            entity_name = 'CreditMemo' if note.tipo_documento == 'CREDITO' else 'Invoice'
            existing = client.find_by_id(entity_name, note.quickbooks_id)
            if existing:
                _mark_synced(note, existing.get('Id'))
                return _sync_result(entity=entity_name, action='existing', payload=existing)

        customer_result = sync_customer(cliente=note.cliente, client=client)
        txn_date = note.fecha.date()
        lines, inventory_presentaciones = _build_adjustment_lines(note, client, txn_date=txn_date)
        entity_name = 'CreditMemo' if note.tipo_documento == 'CREDITO' else 'Invoice'
        existing = _find_transaction_by_doc_number(client, entity_name, note.numero)
        if existing:
            _mark_synced(note, existing.get('Id'))
            return _sync_result(entity=entity_name, action='linked', payload=existing)

        resolved_txn_date = _resolve_txn_date_for_inventory_items(
            client=client,
            presentaciones=inventory_presentaciones,
            txn_date=txn_date,
        )
        payload = {
            'CustomerRef': {'value': customer_result['quickbooks_id']},
            'DocNumber': note.numero,
            'TxnDate': resolved_txn_date.isoformat(),
            'PrivateNote': _truncate(note.descripcion or note.get_motivo_display(), limit=4000),
            'Line': lines,
        }
        if resolved_txn_date != txn_date:
            payload['PrivateNote'] = _truncate(
                f"{payload['PrivateNote']} | Original ERP date: {txn_date.isoformat()}",
                limit=4000,
            )
        if note.invoice_id and note.invoice.quickbooks_id:
            payload['CustomerMemo'] = {'value': f'Related invoice {note.invoice.numero}'}

        if note.tipo_documento == 'CREDITO':
            created = client.create_credit_memo(payload)
        else:
            created = client.create_invoice(payload)
        _mark_synced(note, created.get('Id'))
        return _sync_result(entity=entity_name, action='created', payload=created)
    except (QuickBooksAPIError, QuickBooksSyncError) as exc:
        _mark_failed(note)
        raise QuickBooksSyncError(str(exc)) from exc


def _build_vendor_payload(compra):
    payload = {
        'DisplayName': _truncate(compra.proveedor_nombre, limit=100) or f'Supplier {compra.pk}',
        'CompanyName': _truncate(compra.proveedor_nombre, limit=100) or f'Supplier {compra.pk}',
        'PrintOnCheckName': _truncate(compra.proveedor_nombre, limit=100) or f'Supplier {compra.pk}',
    }
    if compra.proveedor_email:
        payload['PrimaryEmailAddr'] = {'Address': _truncate(compra.proveedor_email, limit=100)}
    if compra.proveedor_telefono:
        payload['PrimaryPhone'] = {'FreeFormNumber': _truncate(compra.proveedor_telefono, limit=21)}
    return payload


def _resolve_vendor_ref_for_purchase(*, compra, client):
    display_name = _truncate(compra.proveedor_nombre, limit=100) or f'Supplier {compra.pk}'
    existing = client.find_one_by_display_name('Vendor', display_name)
    if existing:
        return {
            'value': str(existing.get('Id')),
            'name': existing.get('DisplayName') or existing.get('PrintOnCheckName') or display_name,
        }
    created = client.create_entity('Vendor', _build_vendor_payload(compra))
    return {
        'value': str(created.get('Id')),
        'name': created.get('DisplayName') or created.get('PrintOnCheckName') or display_name,
    }


def _build_purchase_bill_line(*, linea, client):
    product_result = sync_product(presentacion=linea.presentacion, client=client)
    return {
        'DetailType': 'ItemBasedExpenseLineDetail',
        'Amount': _as_float(linea.subtotal),
        'Description': _truncate(
            linea.descripcion or f'{linea.presentacion.producto.nombre} - {linea.presentacion.nombre}',
            limit=4000,
        ),
        'ItemBasedExpenseLineDetail': {
            'ItemRef': _build_item_ref(
                linea.presentacion,
                remote_payload=product_result.get('payload'),
            ),
            'Qty': int(linea.cantidad),
            'UnitPrice': _as_float(linea.costo_unitario),
        },
    }


def _apply_supplier_purchase_inventory(compra):
    registrar_recepcion_compra_proveedor(compra=compra, creado_por=compra.creado_por)


def sync_supplier_purchase(*, compra, client=None):
    client = client or QuickBooksAPIClient()
    if not compra.lineas.exists():
        raise QuickBooksSyncError('Supplier purchase must include at least one line before syncing to QuickBooks.')

    try:
        remote_bill = None
        action = 'created'
        if compra.quickbooks_id:
            remote_bill = client.find_by_id('Bill', compra.quickbooks_id)
            if remote_bill:
                action = 'existing'

        if remote_bill is None and compra.bill_number:
            remote_bill = _find_transaction_by_doc_number(client, 'Bill', compra.bill_number)
            if remote_bill:
                compra.quickbooks_id = str(remote_bill.get('Id') or '')
                compra.save(update_fields=['quickbooks_id'])
                action = 'linked'

        if remote_bill is None:
            vendor_ref = _resolve_vendor_ref_for_purchase(compra=compra, client=client)
            lines = [
                _build_purchase_bill_line(linea=linea, client=client)
                for linea in compra.lineas.select_related('presentacion__producto').all()
            ]
            payload = {
                'VendorRef': vendor_ref,
                'TxnDate': compra.fecha_compra.isoformat(),
                'Line': lines,
                'PrivateNote': _truncate(compra.notas or f'Supplier purchase {compra.pk}', limit=4000),
            }
            if compra.bill_number:
                payload['DocNumber'] = _truncate(compra.bill_number, limit=21)
            if compra.fecha_vencimiento:
                payload['DueDate'] = compra.fecha_vencimiento.isoformat()
            remote_bill = client.create_entity('Bill', payload)
            compra.quickbooks_id = str(remote_bill.get('Id') or '')
            compra.save(update_fields=['quickbooks_id'])

        _mark_synced(compra, remote_bill.get('Id'))
        return _sync_result(entity='Bill', action=action, payload=remote_bill)
    except (QuickBooksAPIError, QuickBooksSyncError, ValidationError) as exc:
        _mark_failed(compra)
        raise QuickBooksSyncError(str(exc)) from exc


def sync_customer_by_id(cliente_id):
    return sync_customer(cliente=Cliente.objects.select_related('usuario').get(pk=cliente_id))


def sync_product_by_id(presentacion_id):
    return sync_product(
        presentacion=Presentacion.objects.select_related('producto', 'stock_operativo').get(pk=presentacion_id)
    )


def sync_invoice_by_id(invoice_id):
    return sync_invoice(
        invoice=Invoice.objects.select_related('cliente__usuario', 'delivery').prefetch_related(
            'items__presentacion__producto',
            'items__presentacion__stock_operativo',
        ).get(pk=invoice_id)
    )


def sync_adjustment_note_by_id(note_id):
    return sync_adjustment_note(note=NotaAjuste.objects.select_related('cliente__usuario', 'invoice').prefetch_related('items__presentacion__producto').get(pk=note_id))


def sync_supplier_purchase_by_id(compra_id):
    return sync_supplier_purchase(
        compra=CompraProveedor.objects.select_related('creado_por').prefetch_related('lineas__presentacion__producto').get(pk=compra_id)
    )


def sync_customer_batch_by_ids(customer_ids, task_cache_key=None):
    return _batch_sync_result(
        record_ids=customer_ids,
        sync_callable=sync_customer_by_id,
        task_cache_key=task_cache_key,
        operation='sync_customers_batch',
    )


def sync_product_batch_by_ids(presentation_ids, task_cache_key=None):
    return _batch_sync_result(
        record_ids=presentation_ids,
        sync_callable=sync_product_by_id,
        task_cache_key=task_cache_key,
        operation='sync_products_batch',
    )


def sync_invoice_batch_by_ids(invoice_ids, task_cache_key=None):
    return _batch_sync_result(
        record_ids=invoice_ids,
        sync_callable=sync_invoice_by_id,
        task_cache_key=task_cache_key,
        operation='sync_invoices_batch',
    )


def sync_adjustment_note_batch_by_ids(note_ids, task_cache_key=None):
    return _batch_sync_result(
        record_ids=note_ids,
        sync_callable=sync_adjustment_note_by_id,
        task_cache_key=task_cache_key,
        operation='sync_adjustment_notes_batch',
    )


def sync_supplier_purchase_batch_by_ids(compra_ids, task_cache_key=None):
    return _batch_sync_result(
        record_ids=compra_ids,
        sync_callable=sync_supplier_purchase_by_id,
        task_cache_key=task_cache_key,
        operation='sync_supplier_purchases_batch',
    )