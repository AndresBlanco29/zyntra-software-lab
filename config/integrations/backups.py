import tarfile
from datetime import datetime
from gzip import open as gzip_open
from gzip import compress as gzip_compress
from io import BytesIO
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import models
from django.utils import timezone


DATABASE_BACKUP_DIRECTORY = Path('backups') / 'database'
SYSTEM_BACKUP_DIRECTORY = Path('backups') / 'system'
MEDIA_ARCHIVE_PREFIX = 'media/'
DATABASE_ARCHIVE_NAME = 'database.json'
BACKUP_TIMESTAMP_FORMAT = '%Y%m%d-%H%M%S'


class DatabaseBackupError(Exception):
    pass


def _build_backup_name(*, label=''):
    timestamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
    normalized_label = str(label or '').strip().replace(' ', '-').replace('_', '-').lower()
    label_segment = f'-{normalized_label}' if normalized_label else ''
    return f'ltg-database-backup{label_segment}-{timestamp}.json.gz'


def _build_system_backup_name(*, label=''):
    timestamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
    normalized_label = str(label or '').strip().replace(' ', '-').replace('_', '-').lower()
    label_segment = f'-{normalized_label}' if normalized_label else ''
    return f'ltg-system-backup{label_segment}-{timestamp}.tar.gz'


def _get_backup_storage():
    if getattr(settings, 'USE_CLOUDINARY_MEDIA', False):
        from cloudinary_storage.storage import RawMediaCloudinaryStorage

        return RawMediaCloudinaryStorage()
    return default_storage


def _database_backup_storage_path(backup_name):
    return str((DATABASE_BACKUP_DIRECTORY / backup_name).as_posix())


def _system_backup_storage_path(backup_name):
    return str((SYSTEM_BACKUP_DIRECTORY / backup_name).as_posix())


def _validate_backup_name(backup_name):
    normalized_name = Path(str(backup_name or '')).name
    if normalized_name != str(backup_name or ''):
        raise DatabaseBackupError('Invalid backup file name.')
    return normalized_name


def _backup_kind_from_name(backup_name):
    normalized_name = _validate_backup_name(backup_name)
    if normalized_name.endswith('.json.gz') or normalized_name.endswith('.json'):
        return 'database', normalized_name
    if normalized_name.endswith('.tar.gz'):
        return 'system', normalized_name
    raise DatabaseBackupError('Unsupported backup file type.')


def _build_database_dump_bytes():
    json_buffer = StringIO()
    call_command('dumpdata', format='json', indent=2, stdout=json_buffer)
    return json_buffer.getvalue().encode('utf-8')


def _iter_local_media_files():
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.exists():
        return
    backups_root = (media_root / 'backups').resolve()
    for file_path in media_root.rglob('*'):
        if not file_path.is_file():
            continue
        resolved_path = file_path.resolve()
        if backups_root.exists() and (resolved_path == backups_root or backups_root in resolved_path.parents):
            continue
        yield file_path


def _iter_referenced_media_files():
    seen_names = set()
    for model in apps.get_models():
        file_fields = [field for field in model._meta.concrete_fields if isinstance(field, models.FileField)]
        if not file_fields:
            continue

        manager = getattr(model, '_default_manager', None)
        if manager is None:
            continue

        for field in file_fields:
            queryset = manager.exclude(**{field.name: ''}).exclude(**{f'{field.name}__isnull': True})
            for file_name in queryset.values_list(field.name, flat=True).iterator():
                normalized_name = str(file_name or '').strip().replace('\\', '/')
                if not normalized_name or normalized_name.startswith('backups/') or normalized_name in seen_names:
                    continue
                seen_names.add(normalized_name)
                yield field.storage, normalized_name


def _backup_modified_time(storage, storage_path, backup_name):
    try:
        return storage.get_modified_time(storage_path)
    except (AttributeError, NotImplementedError, OSError):
        pass

    timestamp_token = backup_name.removesuffix('.tar.gz').removesuffix('.json.gz').rsplit('-', 1)[-1]
    try:
        parsed = datetime.strptime(timestamp_token, BACKUP_TIMESTAMP_FORMAT)
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    except (TypeError, ValueError):
        return timezone.now()


def create_database_backup_file(*, label=''):
    backup_name = _build_backup_name(label=label)
    backup_bytes = gzip_compress(_build_database_dump_bytes())
    backup_storage = _get_backup_storage()
    saved_path = backup_storage.save(_database_backup_storage_path(backup_name), ContentFile(backup_bytes))
    return saved_path, backup_name


def create_system_backup_file(*, label=''):
    backup_name = _build_system_backup_name(label=label)
    dump_bytes = _build_database_dump_bytes()
    seen_archive_names = {DATABASE_ARCHIVE_NAME}
    with NamedTemporaryFile(suffix='.tar.gz', delete=False) as temp_file:
        temp_path = Path(temp_file.name)
    try:
        with tarfile.open(temp_path, mode='w:gz') as archive:
            database_info = tarfile.TarInfo(name=DATABASE_ARCHIVE_NAME)
            database_info.size = len(dump_bytes)
            archive.addfile(database_info, BytesIO(dump_bytes))

            media_root = Path(settings.MEDIA_ROOT)
            for file_path in _iter_local_media_files() or []:
                archive_name = f"{MEDIA_ARCHIVE_PREFIX}{file_path.relative_to(media_root).as_posix()}"
                if archive_name in seen_archive_names:
                    continue
                seen_archive_names.add(archive_name)
                archive.add(file_path, arcname=archive_name)

            for storage, file_name in _iter_referenced_media_files():
                archive_name = f'{MEDIA_ARCHIVE_PREFIX}{file_name}'
                if archive_name in seen_archive_names:
                    continue
                try:
                    with storage.open(file_name, 'rb') as stored_file:
                        file_bytes = stored_file.read()
                except Exception:
                    continue
                seen_archive_names.add(archive_name)
                media_info = tarfile.TarInfo(name=archive_name)
                media_info.size = len(file_bytes)
                archive.addfile(media_info, BytesIO(file_bytes))

        backup_storage = _get_backup_storage()
        saved_path = backup_storage.save(_system_backup_storage_path(backup_name), ContentFile(temp_path.read_bytes()))
    finally:
        temp_path.unlink(missing_ok=True)
    return saved_path, backup_name


def list_database_backups(*, limit=None):
    backup_storage = _get_backup_storage()
    if not backup_storage.exists(str(DATABASE_BACKUP_DIRECTORY.as_posix())):
        return []

    _, file_names = backup_storage.listdir(str(DATABASE_BACKUP_DIRECTORY.as_posix()))
    backups = []
    for backup_name in file_names:
        if not backup_name.endswith('.json.gz'):
            continue
        storage_path = _database_backup_storage_path(backup_name)
        backups.append({
            'name': backup_name,
            'storage_path': storage_path,
            'size_bytes': backup_storage.size(storage_path),
            'modified_at': _backup_modified_time(backup_storage, storage_path, backup_name),
        })

    backups.sort(key=lambda item: item['modified_at'], reverse=True)
    return backups[:limit] if limit is not None else backups


def list_system_backups(*, limit=None):
    backup_storage = _get_backup_storage()
    if not backup_storage.exists(str(SYSTEM_BACKUP_DIRECTORY.as_posix())):
        return []

    _, file_names = backup_storage.listdir(str(SYSTEM_BACKUP_DIRECTORY.as_posix()))
    backups = []
    for backup_name in file_names:
        if not backup_name.endswith('.tar.gz'):
            continue
        storage_path = _system_backup_storage_path(backup_name)
        backups.append({
            'name': backup_name,
            'storage_path': storage_path,
            'size_bytes': backup_storage.size(storage_path),
            'modified_at': _backup_modified_time(backup_storage, storage_path, backup_name),
        })

    backups.sort(key=lambda item: item['modified_at'], reverse=True)
    return backups[:limit] if limit is not None else backups


def open_database_backup(backup_name):
    normalized_name = _validate_backup_name(backup_name)
    storage_path = _database_backup_storage_path(normalized_name)
    backup_storage = _get_backup_storage()
    if not backup_storage.exists(storage_path):
        raise DatabaseBackupError('Backup file not found.')
    return backup_storage.open(storage_path, 'rb'), storage_path, normalized_name


def open_system_backup(backup_name):
    normalized_name = _validate_backup_name(backup_name)
    storage_path = _system_backup_storage_path(normalized_name)
    backup_storage = _get_backup_storage()
    if not backup_storage.exists(storage_path):
        raise DatabaseBackupError('Backup file not found.')
    return backup_storage.open(storage_path, 'rb'), storage_path, normalized_name


def _open_restore_source(source):
    raw_source = str(source or '').strip()
    if not raw_source:
        raise DatabaseBackupError('Backup file is required.')

    local_path = Path(raw_source)
    if local_path.exists() and local_path.is_file():
        return local_path.open('rb'), str(local_path), local_path.name

    backup_kind, _ = _backup_kind_from_name(raw_source)
    if backup_kind == 'system':
        return open_system_backup(raw_source)
    return open_database_backup(raw_source)


def _extract_database_json_from_system_backup(backup_file):
    with tarfile.open(fileobj=backup_file, mode='r:gz') as archive:
        try:
            database_member = archive.getmember(DATABASE_ARCHIVE_NAME)
        except KeyError as exc:
            raise DatabaseBackupError('System backup does not include database.json.') from exc
        extracted = archive.extractfile(database_member)
        if extracted is None:
            raise DatabaseBackupError('System backup database payload could not be read.')
        return extracted.read()


def _restore_media_from_system_backup(backup_file):
    with tarfile.open(fileobj=backup_file, mode='r:gz') as archive:
        for member in archive.getmembers():
            member_name = member.name.replace('\\', '/')
            if not member_name.startswith(MEDIA_ARCHIVE_PREFIX) or member.isdir():
                continue
            relative_name = member_name[len(MEDIA_ARCHIVE_PREFIX):]
            relative_path = Path(relative_name)
            if relative_path.is_absolute() or '..' in relative_path.parts:
                raise DatabaseBackupError('System backup contains an invalid media path.')
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            target_name = relative_path.as_posix()
            if default_storage.exists(target_name):
                default_storage.delete(target_name)
            default_storage.save(target_name, ContentFile(extracted.read()))


def restore_database_backup_file(*, source, flush=False):
    backup_file, _, backup_name = _open_restore_source(source)
    backup_kind, normalized_name = _backup_kind_from_name(backup_name)

    temp_path = None
    try:
        with NamedTemporaryFile(suffix='.json', delete=False) as temp_file:
            temp_path = temp_file.name
            suffixes = Path(normalized_name).suffixes
            if backup_kind == 'system':
                temp_file.write(_extract_database_json_from_system_backup(backup_file))
            elif suffixes[-2:] == ['.json', '.gz']:
                with gzip_open(backup_file, 'rb') as compressed_file:
                    temp_file.write(compressed_file.read())
            else:
                temp_file.write(backup_file.read())

        if flush:
            call_command('flush', interactive=False, verbosity=0, inhibit_post_migrate=True)
        call_command('loaddata', temp_path, verbosity=0)
        if backup_kind == 'system':
            backup_file.seek(0)
            _restore_media_from_system_backup(backup_file)
    except Exception as exc:
        raise DatabaseBackupError(f'Database restore failed: {exc}') from exc
    finally:
        backup_file.close()
        if temp_path is not None:
            Path(temp_path).unlink(missing_ok=True)

    return normalized_name


def prune_database_backups(*, keep=None, backup_type='database'):
    if keep is None:
        return []
    keep = max(int(keep), 0)
    backup_storage = _get_backup_storage()
    backups = list_system_backups(limit=None) if backup_type == 'system' else list_database_backups(limit=None)
    removed = []
    for backup in backups[keep:]:
        backup_storage.delete(backup['storage_path'])
        removed.append(backup['name'])
    return removed