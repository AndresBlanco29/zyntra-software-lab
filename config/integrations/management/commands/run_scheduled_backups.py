from datetime import date

from django.core.management.base import BaseCommand, CommandError

from config.integrations.backups import (
    create_database_backup_file,
    create_system_backup_file,
    prune_database_backups,
)
from config.integrations.quickbooks.services import get_connection


SCHEDULE_VALUES = {'daily', 'weekly', 'monthly'}
DEFAULT_SCHEDULE = 'weekly'


def _normalize_schedule(value):
    normalized = str(value or '').strip().lower()
    return normalized if normalized in SCHEDULE_VALUES else DEFAULT_SCHEDULE


def _resolve_today(raw_value):
    if not raw_value:
        return date.today()
    try:
        return date.fromisoformat(str(raw_value).strip())
    except ValueError as exc:
        raise CommandError('--today must use YYYY-MM-DD format.') from exc


def _schedule_is_due(schedule, today, last_run_on):
    if last_run_on == today.isoformat():
        return False
    if schedule == 'daily':
        return True
    if schedule == 'weekly':
        return today.weekday() == 0
    if schedule == 'monthly':
        return today.day == 1
    return False


class Command(BaseCommand):
    help = 'Run the configured automatic backup schedule for production jobs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--backup-type',
            choices=('system', 'database'),
            default='system',
            help='Backup flavor to generate when the configured cadence is due.',
        )
        parser.add_argument('--keep', type=int, default=14, help='Number of newest backups to keep for the selected backup type.')
        parser.add_argument('--today', default='', help='Optional YYYY-MM-DD override used for deterministic testing or manual backfills.')
        parser.add_argument('--force', action='store_true', help='Create a backup immediately, ignoring the stored cadence and last-run tracking.')

    def handle(self, *args, **options):
        backup_type = options['backup_type']
        keep = int(options.get('keep') or 0)
        if keep < 0:
            raise CommandError('--keep must be zero or greater.')

        today = _resolve_today(options.get('today'))
        connection = get_connection()
        state = dict(connection.sync_state or {})
        schedule = _normalize_schedule(state.get('backup_schedule'))
        automation = dict(state.get('backup_automation') or {})
        backup_state = dict(automation.get(backup_type) or {})
        last_run_on = str(backup_state.get('last_run_on') or '').strip()
        force = bool(options.get('force'))

        if not force and not _schedule_is_due(schedule, today, last_run_on):
            self.stdout.write(
                f'Skipped {backup_type} backup. Configured cadence: {schedule}. '
                f'Today: {today.isoformat()}. Last successful run: {last_run_on or "never"}.'
            )
            return

        try:
            if backup_type == 'database':
                _, backup_name = create_database_backup_file(label=schedule)
                removed = prune_database_backups(keep=keep or None, backup_type='database')
            else:
                _, backup_name = create_system_backup_file(label=schedule)
                removed = prune_database_backups(keep=keep or None, backup_type='system')
        except Exception as exc:
            raise CommandError(f'Automatic {backup_type} backup failed: {exc}') from exc

        backup_state.update(
            {
                'last_run_on': today.isoformat(),
                'last_backup_name': backup_name,
                'schedule': schedule,
            }
        )
        automation[backup_type] = backup_state
        state['backup_automation'] = automation
        connection.sync_state = state
        connection.save(update_fields=['sync_state', 'updated_at'])

        self.stdout.write(self.style.SUCCESS(f'Automatic {backup_type} backup created: {backup_name}'))
        if removed:
            self.stdout.write(f'Old {backup_type} backups removed: {len(removed)}')