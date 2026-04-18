import logging
import threading
from copy import copy

from django.apps import apps
from django.db import connections
from django.db.utils import IntegrityError, OperationalError, ProgrammingError


logger = logging.getLogger(__name__)

_schema_repair_lock = threading.Lock()
_schema_repair_completed = False
_schema_repair_running = False


def _iter_managed_models():
    for model in apps.get_models():
        meta = model._meta
        if meta.proxy or meta.swapped or not meta.managed or meta.auto_created:
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


def _build_relaxed_field(field):
    relaxed_field = copy(field)
    relaxed_field._unique = False
    relaxed_field.null = True
    relaxed_field.blank = True
    relaxed_field.default = None
    return relaxed_field


def _backfill_field_values(connection, model, field):
    table_name = model._meta.db_table
    pk_column = model._meta.pk.column
    column_name = field.column

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT `{pk_column}` FROM `{table_name}` WHERE `{column_name}` IS NULL"
        )
        primary_keys = [row[0] for row in cursor.fetchall()]

    for primary_key in primary_keys:
        value = field.get_default()
        prepared_value = field.get_db_prep_save(value, connection)
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE `{table_name}` SET `{column_name}` = %s WHERE `{pk_column}` = %s",
                [prepared_value, primary_key],
            )


def _add_missing_field(connection, model, field):
    if field.unique and not field.primary_key:
        relaxed_field = _build_relaxed_field(field)
        with connection.schema_editor() as schema_editor:
            schema_editor.add_field(model, relaxed_field)

        _backfill_field_values(connection, model, field)

        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.alter_field(model, relaxed_field, field, strict=False)
        except IntegrityError as exc:
            logger.warning(
                "Runtime schema repair left %s.%s without unique constraint after backfill: %s",
                model._meta.db_table,
                field.column,
                exc,
            )
        return

    with connection.schema_editor() as schema_editor:
        schema_editor.add_field(model, field)


def _create_missing_table(connection, model):
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(model)


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
                _create_missing_table(connection, model)
                table_names.add(table_name)
                logger.warning(
                    "Runtime schema repair created missing table %s",
                    table_name,
                )
                continue

            for field in _iter_missing_concrete_fields(connection, model):
                _add_missing_field(connection, model, field)

                logger.warning(
                    "Runtime schema repair added missing %s.%s column",
                    table_name,
                    field.column,
                )

        with _schema_repair_lock:
            _schema_repair_completed = True
    except (OperationalError, ProgrammingError) as exc:
        message = str(exc).lower()
        if 'duplicate column' in message or '1060' in message or 'already exists' in message or '1050' in message:
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
