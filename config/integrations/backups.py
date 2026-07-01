import json
import logging
import shutil
import tarfile
import threading
import urllib.request
import uuid
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
from django.core.files import File
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management import call_command
from django.db import models
from django.utils import timezone


DATABASE_BACKUP_DIRECTORY = Path('backups') / 'database'
SYSTEM_BACKUP_DIRECTORY = Path('backups') / 'system'
UPLOAD_RESTORE_DIRECTORY = Path('backups') / 'uploads'
RESTORE_JOB_DIRECTORY = Path('backups') / 'restore-jobs'
BACKUP_JOB_DIRECTORY = Path('backups') / 'backup-jobs'
logger = logging.getLogger(__name__)
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
    """Always store backups on local disk (MEDIA_ROOT), not Cloudinary."""
    media_root = Path(settings.MEDIA_ROOT)
    media_root.mkdir(parents=True, exist_ok=True)
    return FileSystemStorage(location=str(media_root))


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


def _write_database_dump_file(target_path):
    with Path(target_path).open('w', encoding='utf-8') as dump_file:
        call_command('dumpdata', format='json', indent=2, stdout=dump_file)


def _cloudinary_media_enabled():
    return bool(getattr(settings, 'USE_CLOUDINARY_MEDIA', False))


def _download_url_to_path(url, target_path, *, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        with Path(target_path).open('wb') as target_file:
            shutil.copyfileobj(response, target_file, length=1024 * 1024)


def _cloudinary_archive_name(resource):
    public_id = str(resource.get('public_id') or '').strip().lstrip('/')
    if not public_id:
        return ''
    archive_name = public_id if public_id.startswith('media/') else f'media/{public_id}'
    resource_format = str(resource.get('format') or '').strip().lower()
    if resource_format and not Path(archive_name).suffix:
        archive_name = f'{archive_name}.{resource_format}'
    return archive_name


def _iter_cloudinary_media_resources():
    if not _cloudinary_media_enabled():
        return

    try:
        from cloudinary import api
    except ImportError:
        logger.warning('Cloudinary SDK is not available for system backup media export.')
        return

    for resource_type in ('image', 'raw', 'video'):
        next_cursor = None
        while True:
            params = {
                'resource_type': resource_type,
                'type': 'upload',
                'max_results': 500,
            }
            if next_cursor:
                params['next_cursor'] = next_cursor
            try:
                response = api.resources(**params)
            except Exception:
                logger.warning('Cloudinary listing failed for resource_type=%s', resource_type, exc_info=True)
                break

            for resource in response.get('resources', []):
                archive_name = _cloudinary_archive_name(resource)
                secure_url = resource.get('secure_url') or resource.get('url')
                if archive_name and secure_url:
                    yield secure_url, archive_name

            next_cursor = response.get('next_cursor')
            if not next_cursor:
                break


def _resolve_cloudinary_download_url(file_name):
    try:
        from cloudinary import api
        from cloudinary.models import CLOUDINARY_FIELD_DB_RE
        from cloudinary.utils import private_download_url
    except ImportError:
        return None

    normalized_name = str(file_name or '').strip().lstrip('/')
    if not normalized_name:
        return None

    public_id_candidates = [normalized_name]
    if normalized_name.startswith('media/'):
        public_id_candidates.append(normalized_name[6:])
    match = CLOUDINARY_FIELD_DB_RE.match(normalized_name)
    if match:
        public_id_candidates.append(match.group('public_id'))

    seen = set()
    for public_id in public_id_candidates:
        public_id = public_id.strip().lstrip('/')
        if not public_id or public_id in seen:
            continue
        seen.add(public_id)

        for resource_type in ('image', 'raw', 'video'):
            for delivery_type in ('upload', 'authenticated', 'private'):
                try:
                    resource = api.resource(
                        public_id,
                        resource_type=resource_type,
                        type=delivery_type,
                    )
                except Exception:
                    continue

                resource_public_id = resource.get('public_id') or public_id
                resource_format = resource.get('format')
                if delivery_type in {'authenticated', 'private'}:
                    try:
                        return private_download_url(
                            resource_public_id,
                            resource_format,
                            resource_type=resource_type,
                            type=delivery_type,
                            secure=True,
                        )
                    except Exception:
                        continue
                secure_url = resource.get('secure_url') or resource.get('url')
                if secure_url:
                    return secure_url
    return None


def _fetch_storage_file_to_path(storage, file_name, target_path):
    try:
        with storage.open(file_name, 'rb') as stored_file:
            with Path(target_path).open('wb') as target_file:
                shutil.copyfileobj(stored_file, target_file, length=1024 * 1024)
        return True
    except Exception:
        pass

    try:
        url = storage.url(file_name)
        if url:
            _download_url_to_path(url, target_path)
            return True
    except Exception:
        pass

    if _cloudinary_media_enabled():
        download_url = _resolve_cloudinary_download_url(file_name)
        if download_url:
            try:
                _download_url_to_path(download_url, target_path)
                return True
            except Exception:
                logger.debug('Cloudinary download failed for %s', file_name, exc_info=True)
    return False


def _add_storage_file_to_archive(archive, storage, file_name, archive_name):
    media_temp_path = None
    try:
        with NamedTemporaryFile(delete=False) as media_temp:
            media_temp_path = Path(media_temp.name)
        if not _fetch_storage_file_to_path(storage, file_name, media_temp_path):
            logger.info('Skipped media file during system backup: %s', file_name)
            return False
        archive.add(media_temp_path, arcname=archive_name)
        return True
    except Exception:
        logger.warning('Skipped media file during system backup: %s', file_name, exc_info=True)
        return False
    finally:
        if media_temp_path is not None:
            media_temp_path.unlink(missing_ok=True)


def _add_url_file_to_archive(archive, download_url, archive_name):
    media_temp_path = None
    try:
        with NamedTemporaryFile(delete=False) as media_temp:
            media_temp_path = Path(media_temp.name)
        _download_url_to_path(download_url, media_temp_path)
        archive.add(media_temp_path, arcname=archive_name)
        return True
    except Exception:
        logger.info('Skipped Cloudinary media during system backup: %s', archive_name)
        return False
    finally:
        if media_temp_path is not None:
            media_temp_path.unlink(missing_ok=True)


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
    database_path = None
    temp_path = None
    try:
        with NamedTemporaryFile(suffix='.json', delete=False) as database_temp:
            database_path = Path(database_temp.name)
        _write_database_dump_file(database_path)

        with NamedTemporaryFile(suffix='.tar.gz', delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        seen_archive_names = set()
        with tarfile.open(temp_path, mode='w:gz') as archive:
            archive.add(database_path, arcname=DATABASE_ARCHIVE_NAME)
            seen_archive_names.add(DATABASE_ARCHIVE_NAME)

            media_root = Path(settings.MEDIA_ROOT)
            for file_path in _iter_local_media_files() or []:
                archive_name = f"{MEDIA_ARCHIVE_PREFIX}{file_path.relative_to(media_root).as_posix()}"
                if archive_name in seen_archive_names:
                    continue
                seen_archive_names.add(archive_name)
                archive.add(file_path, arcname=archive_name)

            if _cloudinary_media_enabled():
                for secure_url, archive_name in _iter_cloudinary_media_resources():
                    if archive_name in seen_archive_names:
                        continue
                    if _add_url_file_to_archive(archive, secure_url, archive_name):
                        seen_archive_names.add(archive_name)
            else:
                for storage, file_name in _iter_referenced_media_files():
                    archive_name = f'{MEDIA_ARCHIVE_PREFIX}{file_name}'
                    if archive_name in seen_archive_names:
                        continue
                    if _add_storage_file_to_archive(archive, storage, file_name, archive_name):
                        seen_archive_names.add(archive_name)

        backup_storage = _get_backup_storage()
        with temp_path.open('rb') as backup_file:
            saved_path = backup_storage.save(_system_backup_storage_path(backup_name), File(backup_file))
        logger.info('System backup created: %s (%s bytes)', backup_name, temp_path.stat().st_size)
    finally:
        if database_path is not None:
            database_path.unlink(missing_ok=True)
        if temp_path is not None:
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


ALLOWED_BACKUP_UPLOAD_SUFFIXES = ('.json.gz', '.json', '.tar.gz')
MAX_BACKUP_UPLOAD_BYTES = 200 * 1024 * 1024


def _validate_uploaded_backup_file(uploaded_file):
    if uploaded_file is None:
        raise DatabaseBackupError('Backup file is required.')
    original_name = Path(str(uploaded_file.name or '')).name
    if not original_name:
        raise DatabaseBackupError('Backup file name is required.')
    normalized_name = original_name.lower()
    if not any(normalized_name.endswith(suffix) for suffix in ALLOWED_BACKUP_UPLOAD_SUFFIXES):
        raise DatabaseBackupError('Unsupported backup file type. Use .json.gz, .json, or .tar.gz.')
    size = getattr(uploaded_file, 'size', None)
    if size is not None and int(size) > MAX_BACKUP_UPLOAD_BYTES:
        raise DatabaseBackupError('Backup file is too large (max 200 MB).')
    return _backup_kind_from_name(original_name)[1]


def persist_uploaded_backup_for_restore(uploaded_file):
    backup_name = _validate_uploaded_backup_file(uploaded_file)
    upload_root = Path(settings.MEDIA_ROOT) / UPLOAD_RESTORE_DIRECTORY
    upload_root.mkdir(parents=True, exist_ok=True)
    dest_path = upload_root / f'{uuid.uuid4().hex}-{backup_name}'
    total_bytes = 0
    with dest_path.open('wb') as dest_file:
        for chunk in uploaded_file.chunks():
            total_bytes += len(chunk)
            if total_bytes > MAX_BACKUP_UPLOAD_BYTES:
                dest_path.unlink(missing_ok=True)
                raise DatabaseBackupError('Backup file is too large (max 200 MB).')
            dest_file.write(chunk)
    return dest_path


def restore_database_backup_upload(*, uploaded_file, flush=False):
    temp_path = persist_uploaded_backup_for_restore(uploaded_file)
    try:
        return restore_database_backup_file(source=str(temp_path), flush=flush)
    finally:
        temp_path.unlink(missing_ok=True)


def _backup_job_status_path(job_id):
    normalized_job_id = Path(str(job_id or '').strip()).name
    if not normalized_job_id or normalized_job_id != str(job_id or '').strip():
        raise DatabaseBackupError('Invalid backup job id.')
    job_root = Path(settings.MEDIA_ROOT) / BACKUP_JOB_DIRECTORY
    job_root.mkdir(parents=True, exist_ok=True)
    return job_root / f'{normalized_job_id}.json'


def _write_backup_job_status(job_id, payload):
    status_path = _backup_job_status_path(job_id)
    status_path.write_text(json.dumps(payload), encoding='utf-8')


def get_system_backup_job(job_id):
    try:
        status_path = _backup_job_status_path(job_id)
    except DatabaseBackupError:
        return None
    if not status_path.exists():
        return None
    try:
        return json.loads(status_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def start_system_backup_job(*, label=''):
    job_id = uuid.uuid4().hex

    def _runner():
        from django.db import close_old_connections

        close_old_connections()
        try:
            _write_backup_job_status(job_id, {'status': 'running', 'phase': 'database'})
            saved_path, backup_name = create_system_backup_file(label=label)
            _write_backup_job_status(
                job_id,
                {
                    'status': 'completed',
                    'phase': 'done',
                    'backup_name': backup_name,
                    'saved_path': saved_path,
                },
            )
        except Exception as exc:
            logger.exception('Background system backup failed: %s', exc)
            _write_backup_job_status(job_id, {'status': 'failed', 'error': str(exc)})
        finally:
            close_old_connections()

    _write_backup_job_status(job_id, {'status': 'running', 'phase': 'queued'})
    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return job_id


def _restore_job_status_path(job_id):
    normalized_job_id = Path(str(job_id or '').strip()).name
    if not normalized_job_id or normalized_job_id != str(job_id or '').strip():
        raise DatabaseBackupError('Invalid restore job id.')
    job_root = Path(settings.MEDIA_ROOT) / RESTORE_JOB_DIRECTORY
    job_root.mkdir(parents=True, exist_ok=True)
    return job_root / f'{normalized_job_id}.json'


def _write_restore_job_status(job_id, payload):
    status_path = _restore_job_status_path(job_id)
    status_path.write_text(json.dumps(payload), encoding='utf-8')


def get_database_restore_job(job_id):
    try:
        status_path = _restore_job_status_path(job_id)
    except DatabaseBackupError:
        return None
    if not status_path.exists():
        return None
    try:
        return json.loads(status_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def start_database_restore_job(*, source, flush=False, cleanup_source=False):
    job_id = uuid.uuid4().hex
    source_value = str(source)

    def _runner():
        try:
            _write_restore_job_status(job_id, {'status': 'running', 'phase': 'restore'})
            backup_name = restore_database_backup_file(source=source_value, flush=flush)
            _write_restore_job_status(job_id, {'status': 'completed', 'backup_name': backup_name})
        except Exception as exc:
            logger.exception('Background database restore failed: %s', exc)
            _write_restore_job_status(job_id, {'status': 'failed', 'error': str(exc)})
        finally:
            if cleanup_source:
                Path(source_value).unlink(missing_ok=True)

    _write_restore_job_status(job_id, {'status': 'running', 'phase': 'queued'})
    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return job_id


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