"""User-agent parsing and audit enrichment helpers (no external deps)."""

from __future__ import annotations

import re
from typing import Any


def parse_user_agent(user_agent: str) -> dict[str, str]:
    ua = (user_agent or '').strip()
    if not ua:
        return {'browser': '', 'os_name': '', 'device': ''}

    browser = ''
    if 'Edg/' in ua:
        browser = 'Microsoft Edge'
    elif 'OPR/' in ua or 'Opera' in ua:
        browser = 'Opera'
    elif 'Chrome/' in ua and 'Chromium' not in ua:
        browser = 'Chrome'
    elif 'Firefox/' in ua:
        browser = 'Firefox'
    elif 'Safari/' in ua and 'Chrome/' not in ua:
        browser = 'Safari'
    elif 'MSIE' in ua or 'Trident/' in ua:
        browser = 'Internet Explorer'
    else:
        browser = 'Unknown browser'

    os_name = ''
    if 'Windows NT 10' in ua or 'Windows NT 11' in ua:
        os_name = 'Windows'
    elif 'Windows' in ua:
        os_name = 'Windows'
    elif 'Android' in ua:
        os_name = 'Android'
    elif 'iPhone' in ua or 'iPad' in ua or 'iOS' in ua:
        os_name = 'iOS'
    elif 'Mac OS X' in ua or 'Macintosh' in ua:
        os_name = 'macOS'
    elif 'Linux' in ua:
        os_name = 'Linux'
    else:
        os_name = 'Unknown OS'

    device = 'Desktop'
    if 'iPad' in ua or 'Tablet' in ua:
        device = 'Tablet'
    elif 'Mobi' in ua or 'iPhone' in ua or 'Android' in ua:
        device = 'Mobile'

    return {
        'browser': browser[:80],
        'os_name': os_name[:80],
        'device': device[:40],
    }


def resolve_module(*, route_name: str = '', path: str = '', entity_type: str = '') -> str:
    if entity_type:
        return str(entity_type)[:80]

    route = (route_name or '').lower()
    path_l = (path or '').lower()

    mapping = (
        (('reportes', 'reports'), 'Reports'),
        (('quickbooks',), 'QuickBooks'),
        (('pedido', 'order'), 'Orders'),
        (('invoice', 'factura', 'facturacion'), 'Invoices'),
        (('delivery', 'driver'), 'Deliveries'),
        (('producto', 'product', 'pricing', 'marca', 'categoria'), 'Products'),
        (('cliente', 'customer'), 'Customers'),
        (('usuario', 'user', 'vendedor'), 'Users'),
        (('promo',), 'Promotions'),
        (('inventario', 'stock', 'compra', 'purchase', 'supplier'), 'Inventory'),
        (('auditoria', 'audit'), 'Audit'),
        (('login', 'logout'), 'Authentication'),
        (('cotizacion', 'quote'), 'Quotes'),
        (('home', 'panel_admin'), 'Admin'),
    )
    haystack = f'{route} {path_l}'
    for tokens, label in mapping:
        if any(token in haystack for token in tokens):
            return label
    return 'System'


def normalize_changes(changes: Any = None, metadata: dict | None = None) -> list[dict]:
    """Return a list of {field, before, after} dicts from explicit changes or metadata heuristics."""
    normalized: list[dict] = []
    seen = set()

    def _add(field, before, after):
        key = (str(field), str(before), str(after))
        if key in seen:
            return
        if before is None and after is None:
            return
        if str(before) == str(after):
            return
        seen.add(key)
        normalized.append({
            'field': str(field)[:120],
            'before': '' if before is None else str(before)[:500],
            'after': '' if after is None else str(after)[:500],
        })

    if isinstance(changes, list):
        for item in changes:
            if not isinstance(item, dict):
                continue
            _add(item.get('field') or item.get('label') or 'Field', item.get('before'), item.get('after'))

    meta = metadata or {}
    if isinstance(meta.get('changes'), list):
        for item in meta['changes']:
            if isinstance(item, dict):
                _add(item.get('field') or item.get('label') or 'Field', item.get('before'), item.get('after'))

    pair_map = (
        ('estado_anterior', 'estado_nuevo', 'Status'),
        ('precio_antes', 'precio_despues', 'Price'),
        ('stock_antes', 'stock_despues', 'Stock'),
        ('before', 'after', 'Value'),
    )
    for before_key, after_key, label in pair_map:
        if before_key in meta or after_key in meta:
            _add(label, meta.get(before_key), meta.get(after_key))

    if isinstance(meta.get('diff'), dict):
        for field, payload in meta['diff'].items():
            if isinstance(payload, dict):
                _add(field, payload.get('before'), payload.get('after'))
            elif isinstance(payload, (list, tuple)) and len(payload) == 2:
                _add(field, payload[0], payload[1])

    return normalized[:100]


def build_line_item_changes(before_items: list[dict], after_items: list[dict]) -> list[dict]:
    """Compare order line snapshots and return human-readable field changes."""
    changes: list[dict] = []
    before_by_id = {str(item.get('item_id')): item for item in before_items if item.get('item_id') is not None}
    after_by_id = {str(item.get('item_id')): item for item in after_items if item.get('item_id') is not None}

    for item_id, after in after_by_id.items():
        label = f"{after.get('producto') or 'Item'} ({after.get('presentacion') or ''})".strip()
        before = before_by_id.get(item_id)
        if before is None:
            changes.append({
                'field': f'Added: {label}',
                'before': '',
                'after': f"qty {after.get('cantidad')} @ {after.get('precio')}",
            })
            continue
        if str(before.get('cantidad')) != str(after.get('cantidad')):
            changes.append({
                'field': f'Quantity: {label}',
                'before': before.get('cantidad'),
                'after': after.get('cantidad'),
            })
        if str(before.get('precio')) != str(after.get('precio')):
            changes.append({
                'field': f'Price: {label}',
                'before': before.get('precio'),
                'after': after.get('precio'),
            })
        if str(before.get('descuento_monto')) != str(after.get('descuento_monto')) or bool(before.get('descuento_aplicado')) != bool(after.get('descuento_aplicado')):
            changes.append({
                'field': f'Discount: {label}',
                'before': before.get('descuento_monto'),
                'after': after.get('descuento_monto'),
            })

    for item_id, before in before_by_id.items():
        if item_id not in after_by_id:
            label = f"{before.get('producto') or 'Item'} ({before.get('presentacion') or ''})".strip()
            changes.append({
                'field': f'Removed: {label}',
                'before': f"qty {before.get('cantidad')} @ {before.get('precio')}",
                'after': '',
            })
    return changes


def geo_hint_from_ip(ip_address: str | None) -> dict[str, str]:
    """Lightweight geo hint without external network calls."""
    ip = (ip_address or '').strip()
    if not ip:
        return {'geo_city': '', 'geo_country': ''}
    if ip.startswith(('127.', '10.', '192.168.', '172.')) or ip == '::1':
        return {'geo_city': 'Local network', 'geo_country': ''}
    return {'geo_city': '', 'geo_country': ''}
