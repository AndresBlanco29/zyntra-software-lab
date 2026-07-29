"""Ensure MySQL stock columns accept negative QuickBooks QtyOnHand values.

PositiveIntegerField historically created UNSIGNED columns. Django AlterField to
IntegerField can be marked applied while the live column remains UNSIGNED, which
makes imports fail with:

    (1264, "Out of range value for column 'stock_fisico' at row 1")

and leaves Tortilla showing stale positive Quick Inventory.
"""

from __future__ import annotations

import logging

from django.db import connection

logger = logging.getLogger(__name__)

_STOCK_PRESENTACION_COLUMNS = ('stock_fisico', 'stock_disponible')
_MOVIMIENTO_COLUMNS = (
    'stock_fisico_anterior',
    'stock_fisico_posterior',
    'stock_disponible_anterior',
    'stock_disponible_posterior',
)


def _mysql_column_is_unsigned(table_name, column_name):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            """,
            [table_name, column_name],
        )
        row = cursor.fetchone()
    if not row:
        return False
    return 'unsigned' in str(row[0] or '').lower()


def stock_columns_reject_negatives():
    """Return True when MySQL still has UNSIGNED stock columns."""
    if connection.vendor != 'mysql':
        return False
    for column_name in _STOCK_PRESENTACION_COLUMNS:
        if _mysql_column_is_unsigned('inventario_stockpresentacion', column_name):
            return True
    for column_name in _MOVIMIENTO_COLUMNS:
        if _mysql_column_is_unsigned('inventario_inventariomovimiento', column_name):
            return True
    return False


def ensure_signed_stock_columns(*, force=False):
    """Alter stock columns to signed INTEGER when needed (MySQL only).

    Returns True when an ALTER TABLE ran, False when nothing changed / skipped.
    MySQL DDL cannot run inside an atomic block; callers should invoke this
    outside transactions (e.g. start of inventory quantity import, or a
    non-atomic migration).
    """
    if connection.vendor != 'mysql':
        return False
    if not force and not stock_columns_reject_negatives():
        return False
    if connection.in_atomic_block:
        logger.error(
            'Cannot ALTER inventario stock columns while inside an atomic block. '
            'Run migrate inventario.0017_ensure_signed_stock_columns_again (or retry '
            'inventory quantity import outside a transaction).'
        )
        return False

    with connection.cursor() as cursor:
        cursor.execute(
            """
            ALTER TABLE inventario_stockpresentacion
                MODIFY stock_fisico INTEGER NOT NULL DEFAULT 0,
                MODIFY stock_disponible INTEGER NOT NULL DEFAULT 0
            """
        )
        cursor.execute(
            """
            ALTER TABLE inventario_inventariomovimiento
                MODIFY stock_fisico_anterior INTEGER NOT NULL DEFAULT 0,
                MODIFY stock_fisico_posterior INTEGER NOT NULL DEFAULT 0,
                MODIFY stock_disponible_anterior INTEGER NOT NULL DEFAULT 0,
                MODIFY stock_disponible_posterior INTEGER NOT NULL DEFAULT 0
            """
        )
    logger.warning(
        'Altered inventario stock columns to signed INTEGER so QuickBooks negative QtyOnHand can sync.'
    )
    return True


def is_out_of_range_stock_error(exc):
    """Detect MySQL 1264 / Django DataError for UNSIGNED stock columns."""
    text = str(exc or '')
    lowered = text.lower()
    if '1264' in text and 'stock_' in lowered:
        return True
    if 'out of range value for column' in lowered and 'stock_' in lowered:
        return True
    return False
