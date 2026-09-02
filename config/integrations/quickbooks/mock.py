"""QuickBooks mock provider for DEMO_MODE / Software Lab (no external network)."""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from config.clientes.models import Cliente
from config.facturacion.models import Invoice
from config.integrations.models import QuickBooksConnection, QuickBooksSyncRun
from config.productos.models import Presentacion


MOCK_STAGES = (
    ('Connecting...', 8),
    ('Authenticating...', 18),
    ('Validating company...', 30),
    ('Comparing records...', 45),
    ('Syncing customers...', 60),
    ('Syncing products...', 75),
    ('Syncing invoices...', 88),
    ('Finalizing...', 96),
)


def is_quickbooks_mock_enabled() -> bool:
    return bool(getattr(settings, 'DEMO_MODE', False)) and (
        str(getattr(settings, 'QUICKBOOKS_PROVIDER', '') or '').strip().lower() == 'mock'
    )


def ensure_mock_connection() -> QuickBooksConnection:
    environment = (getattr(settings, 'QUICKBOOKS_ENVIRONMENT', None) or 'sandbox').strip().lower()
    connection, _ = QuickBooksConnection.objects.update_or_create(
        environment=environment,
        defaults={
            'realm_id': 'demo-mock-realm-0001',
            'access_token': 'demo-mock-access-token',
            'refresh_token': 'demo-mock-refresh-token',
            'token_type': 'Bearer',
            'scope': 'com.intuit.quickbooks.accounting',
            'access_token_expires_at': timezone.now() + timedelta(hours=1),
            'refresh_token_expires_at': timezone.now() + timedelta(days=100),
            'connected_at': timezone.now() - timedelta(days=1),
            'last_refreshed_at': timezone.now(),
            'last_error': '',
            'sync_state': {
                'demo_mock': True,
                'provider': 'mock',
                'label': 'Zyntra Software Lab mock QuickBooks',
            },
        },
    )
    return connection


def _local_counts():
    return {
        'customers': Cliente.objects.count(),
        'products': Presentacion.objects.count(),
        'invoices': Invoice.objects.count(),
        'payments': Invoice.objects.exclude(qb_payment_status='').count(),
    }


def run_mock_synchronization(*, operation='alignment_sync_to_local', force_full=False, task_cache_key=None):
    """Simulate a realistic sync timeline and write history rows locally."""
    ensure_mock_connection()
    counts = _local_counts()
    started = timezone.now()
    run = QuickBooksSyncRun.objects.create(
        trigger=(
            QuickBooksSyncRun.TRIGGER_MANUAL_FULL
            if force_full
            else QuickBooksSyncRun.TRIGGER_MANUAL
        ),
        status=QuickBooksSyncRun.STATUS_RUNNING,
        force_full=force_full,
        summary={'demo_mock': True, 'operation': operation, 'stages': []},
    )

    stages_done = []
    for label, progress in MOCK_STAGES:
        stages_done.append(label)
        if task_cache_key:
            cache.set(
                task_cache_key,
                {
                    'status': 'running',
                    'progress': progress,
                    'operation': operation,
                    'message': label,
                    'demo_mock': True,
                },
                timeout=60 * 60,
            )
        time.sleep(0.12)

    finished = timezone.now()
    summary = {
        'demo_mock': True,
        'operation': operation,
        'force_full': force_full,
        'stages': stages_done,
        'import': {
            'customers': {'created': 0, 'updated': counts['customers']},
            'items': {'created': 0, 'updated': counts['products']},
            'invoices': {'created': 0, 'updated': 0},
        },
        'export': {
            'customers': {'success': 0, 'failed': 0},
            'presentations': {'success': 0, 'failed': 0},
            'items': {'success': 0, 'failed': 0},
            'invoices': {'success': min(counts['invoices'], 12), 'failed': 0},
            'payments': {'success': min(counts['payments'], 10), 'failed': 0},
        },
        'records_processed': counts['customers'] + counts['products'] + counts['invoices'],
        'created': 0,
        'updated': counts['customers'] + min(counts['invoices'], 12),
        'skipped': 0,
        'failed': 0,
        'warnings': [],
        'duration_seconds': max(1, int((finished - started).total_seconds())),
    }
    QuickBooksSyncRun.objects.filter(pk=run.pk).update(
        status=QuickBooksSyncRun.STATUS_SUCCESS,
        summary=summary,
        started_at=started,
        finished_at=finished,
    )
    if task_cache_key:
        cache.set(
            task_cache_key,
            {
                'status': 'completed',
                'progress': 100,
                'operation': operation,
                'message': 'Synchronization completed.',
                'result': summary,
                'demo_mock': True,
            },
            timeout=60 * 60,
        )
    return {
        'success': True,
        'demo_mock': True,
        'message': 'Synchronization completed.',
        'summary': summary,
        'run_id': run.pk,
        'customers': counts['customers'],
        'items': counts['products'],
        'invoices': counts['invoices'],
    }


def start_mock_background_task(*, operation, force_full=False):
    task_id = uuid.uuid4().hex
    cache_key = f'quickbooks_task_{task_id}'
    cache.set(
        cache_key,
        {
            'status': 'running',
            'progress': 0,
            'operation': operation,
            'message': 'Connecting...',
            'demo_mock': True,
        },
        timeout=60 * 60,
    )

    def _runner():
        try:
            run_mock_synchronization(
                operation=operation,
                force_full=force_full,
                task_cache_key=cache_key,
            )
        except Exception as exc:  # pragma: no cover - defensive
            cache.set(
                cache_key,
                {
                    'status': 'failed',
                    'progress': 100,
                    'operation': operation,
                    'error': str(exc),
                    'demo_mock': True,
                },
                timeout=60 * 60,
            )

    import threading

    threading.Thread(target=_runner, daemon=True).start()
    return task_id
