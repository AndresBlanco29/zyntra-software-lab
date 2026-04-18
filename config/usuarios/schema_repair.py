import logging

from django.apps import apps
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError


logger = logging.getLogger(__name__)


def ensure_permission_overrides_column_on_startup():
    connection = connections['default']
    if connection.vendor != 'mysql':
        return

    usuario_model = apps.get_model('usuarios', 'Usuario')
    table_name = usuario_model._meta.db_table

    try:
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))

        if table_name not in table_names:
            return

        with connection.cursor() as cursor:
            existing_columns = {
                column.name
                for column in connection.introspection.get_table_description(cursor, table_name)
            }

        if 'permission_overrides' in existing_columns:
            return

        with connection.cursor() as cursor:
            cursor.execute(
                f"ALTER TABLE `{table_name}` ADD COLUMN `permission_overrides` JSON NULL"
            )
            cursor.execute(
                f"UPDATE `{table_name}` SET `permission_overrides` = JSON_OBJECT() WHERE `permission_overrides` IS NULL"
            )

        logger.warning(
            "Runtime schema repair added missing %s.permission_overrides column",
            table_name,
        )
    except (OperationalError, ProgrammingError) as exc:
        message = str(exc).lower()
        if 'duplicate column' in message or '1060' in message:
            return
        logger.exception(
            "Runtime schema repair failed for %s.permission_overrides: %s",
            table_name,
            exc,
        )
