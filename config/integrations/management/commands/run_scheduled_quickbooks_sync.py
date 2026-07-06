from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from config.integrations.models import QuickBooksSyncRun
from config.integrations.quickbooks.alignment_sync import (
    alignment_slot_is_due,
    alignment_timezone,
    mark_alignment_slot_completed,
    record_skipped_alignment_run,
    run_quickbooks_alignment_sync,
    SCHEDULED_ALIGNMENT_HOURS,
)
from config.integrations.quickbooks.services import get_connection


def _resolve_now(raw_value):
    if not raw_value:
        return datetime.now(alignment_timezone())
    try:
        parsed = datetime.fromisoformat(str(raw_value).strip())
    except ValueError as exc:
        raise CommandError('--now must use ISO format, for example 2026-07-03T06:00:00.') from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=alignment_timezone())
    return parsed.astimezone(alignment_timezone())


class Command(BaseCommand):
    help = (
        'Run the QuickBooks alignment sync on the 6-hour schedule '
        '(6 AM, 12 PM, 6 PM, and 12 AM US Eastern).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Run immediately, ignoring the schedule window and last-slot tracking. Also runs a full import.',
        )
        parser.add_argument(
            '--now',
            default='',
            help='Optional ISO datetime override for deterministic testing (US Eastern).',
        )

    def handle(self, *args, **options):
        connection = get_connection()
        if not connection.is_active:
            self.stdout.write('Skipped QuickBooks alignment sync: no active QuickBooks connection.')
            return

        now = _resolve_now(options.get('now'))
        force = bool(options.get('force'))
        due, slot_key = alignment_slot_is_due(now=now, force=force)

        if not due:
            self.stdout.write(
                f'Skipped QuickBooks alignment sync. Local time: {now.isoformat()}. '
                f'Allowed hours: {sorted(SCHEDULED_ALIGNMENT_HOURS)}. '
                f'Last completed slot: {(connection.sync_state or {}).get("alignment_automation", {}).get("last_slot", "never")}.'
            )
            return

        self.stdout.write(
            f'Starting QuickBooks alignment sync for slot {slot_key} ({now.isoformat()}).'
        )
        try:
            result = run_quickbooks_alignment_sync(
                force_full=force,
                trigger=QuickBooksSyncRun.TRIGGER_SCHEDULED,
                max_results=None,
                save_history=True,
                scheduled_slot=slot_key,
                include_export=False,
            )
        except Exception as exc:
            raise CommandError(f'QuickBooks alignment sync failed: {exc}') from exc

        if not force:
            mark_alignment_slot_completed(slot_key=slot_key)

        summary = result.get('summary') or {}
        import_summary = summary.get('import') or {}
        export_summary = summary.get('export') or {}
        customers = import_summary.get('customers') or {}
        items = import_summary.get('items') or {}
        invoice_status = import_summary.get('invoice_status') or {}
        export_customers = export_summary.get('customers') or {}
        export_items = export_summary.get('presentations') or {}

        self.stdout.write(self.style.SUCCESS('QuickBooks alignment sync complete.'))
        self.stdout.write(
            f"Import customers -> created {customers.get('created', 0)}, updated {customers.get('updated', 0)}."
        )
        self.stdout.write(
            f"Import catalog -> created {items.get('created', 0)}, updated {items.get('updated', 0)}."
        )
        self.stdout.write(
            f"Invoice payment status -> updated {invoice_status.get('updated', 0)} of {invoice_status.get('linked', 0)} linked."
        )
        if export_summary.get('skipped'):
            self.stdout.write('Export skipped (manual export only).')
        else:
            self.stdout.write(
                f"Export new customers -> sent {export_customers.get('success', 0)}, failed {export_customers.get('failed', 0)}."
            )
            self.stdout.write(
                f"Export new products -> sent {export_items.get('success', 0)}, failed {export_items.get('failed', 0)}."
            )
        self.stdout.write('Catalog import updates local inventory from QuickBooks quantities.')
        self.stdout.write('Invoices were not exported automatically (manual only).')
