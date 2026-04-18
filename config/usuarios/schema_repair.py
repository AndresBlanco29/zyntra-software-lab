import logging
import threading

from django.apps import apps
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError


logger = logging.getLogger(__name__)

_schema_repair_lock = threading.Lock()
_schema_repair_attempted = False


REPAIRS = (
    ('usuarios', 'Usuario', 'permission_overrides'),
    ('clientes', 'Cliente', 'declaracion_fiscal_aceptada'),
    ('clientes', 'Cliente', 'declaracion_fiscal_aceptada_en'),
)


def ensure_runtime_schema():
    global _schema_repair_attempted

    with _schema_repair_lock:
        if _schema_repair_attempted:
            return
        _schema_repair_attempted = True

    connection = connections['default']
    if connection.vendor != 'mysql':
        return

    try:
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))

        for app_label, model_name, field_name in REPAIRS:
            model = apps.get_model(app_label, model_name)
            table_name = model._meta.db_table
            if table_name not in table_names:
                continue

            with connection.cursor() as cursor:
                existing_columns = {
                    column.name
                    for column in connection.introspection.get_table_description(cursor, table_name)
                }

            if field_name in existing_columns:
                continue

            field = model._meta.get_field(field_name)
            with connection.schema_editor() as schema_editor:
                schema_editor.add_field(model, field)

            logger.warning(
                "Runtime schema repair added missing %s.%s column",
                table_name,
                field_name,
            )
    except (OperationalError, ProgrammingError) as exc:
        message = str(exc).lower()
        if 'duplicate column' in message or '1060' in message:
            return
        logger.exception(
            "Runtime schema repair failed: %s",
            exc,
        )


def ensure_permission_overrides_column_on_startup():
    ensure_runtime_schema()
