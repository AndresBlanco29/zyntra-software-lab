from django.core.management.base import BaseCommand, CommandError

from config.integrations.backups import create_system_backup_file, prune_database_backups


class Command(BaseCommand):
    help = 'Create a full system backup with database records and uploaded media files.'

    def add_arguments(self, parser):
        parser.add_argument('--label', default='', help='Optional label for the backup name, such as daily or weekly.')
        parser.add_argument('--keep', type=int, default=0, help='Optional number of newest full backups to keep. Use 0 to keep all backups.')

    def handle(self, *args, **options):
        keep = int(options.get('keep') or 0)
        if keep < 0:
            raise CommandError('--keep must be zero or greater.')

        try:
            _, backup_name = create_system_backup_file(label=options.get('label') or '')
            removed = prune_database_backups(keep=keep or None, backup_type='system')
        except Exception as exc:
            raise CommandError(f'System backup failed: {exc}') from exc

        self.stdout.write(self.style.SUCCESS(f'System backup created: {backup_name}'))
        if removed:
            self.stdout.write(f'Old system backups removed: {len(removed)}')