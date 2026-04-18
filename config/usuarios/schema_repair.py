import logging
import threading

from django.apps import apps
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError


logger = logging.getLogger(__name__)

_schema_repair_lock = threading.Lock()
_schema_repair_completed = False
_schema_repair_running = False


def _iter_managed_models():
    for model in apps.get_models():
        meta = model._meta
        if meta.proxy or meta.swapped or not meta.managed:
            continue
        yield model


def _iter_missing_concrete_fields(connection, model):
    table_name = model._meta.db_table

    with connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in connection.introspection.get_table_description(cursor, table_name)
        }

    for field in model._meta.concrete_fields:
        if field.primary_key or field.many_to_many or not field.column:
            continue
        if field.column in existing_columns:
            continue
        yield field


def ensure_runtime_schema():
    global _schema_repair_completed, _schema_repair_running

    with _schema_repair_lock:
        if _schema_repair_completed or _schema_repair_running:
            return
        _schema_repair_running = True

    connection = connections['default']
    if connection.vendor != 'mysql':
        with _schema_repair_lock:
            _schema_repair_completed = True
            _schema_repair_running = False
        return

    try:
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))

        for model in _iter_managed_models():
            table_name = model._meta.db_table
            if table_name not in table_names:
                continue

            for field in _iter_missing_concrete_fields(connection, model):
                with connection.schema_editor() as schema_editor:
                    schema_editor.add_field(model, field)

                logger.warning(
                    "Runtime schema repair added missing %s.%s column",
                    table_name,
                    field.column,
                )

        with _schema_repair_lock:
            _schema_repair_completed = True
    except (OperationalError, ProgrammingError) as exc:
        message = str(exc).lower()
        if 'duplicate column' in message or '1060' in message:
            return
        logger.exception(
            "Runtime schema repair failed: %s",
            exc,
        )
    finally:
        with _schema_repair_lock:
            _schema_repair_running = False


def ensure_permission_overrides_column_on_startup():
    ensure_runtime_schema()
