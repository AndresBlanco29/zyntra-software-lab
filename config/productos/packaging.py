import re
import unicodedata

COUNT_UNIT_TOKENS = r'CT|PK|PC|EA'

PACK_UNIT_TOKENS = (
    rf'LT|LTR|L|ML|OZ|FLOZ|FL\s*OZ|GAL|GALON|GALONES|KG|LB|GR|G|{COUNT_UNIT_TOKENS}'
)

CASE_PACK_PATTERN = re.compile(
    rf'(\d+)\s*/\s*([\d.]+)\s*({PACK_UNIT_TOKENS})\b',
    re.IGNORECASE,
)

SIMPLE_COUNT_PATTERN = re.compile(
    rf'(\d+)\s+({COUNT_UNIT_TOKENS})\b',
    re.IGNORECASE,
)

SIZE_HINT_PATTERN = re.compile(
    rf'\d|\b(LT|LTR|OZ|FLOZ|ML|GAL|GALON|KG|LB|GR|G|CT|PK|PC|EA)\b',
    re.IGNORECASE,
)

GENERIC_CONTENT_TYPES = {
    'unidad', 'unidades', 'unit', 'units',
    'pieza', 'piezas', 'piece', 'pieces',
    'caja', 'cajas', 'box', 'boxes',
    'pallet', 'pallets', 'pack', 'packs',
}

COUNT_CONTENT_TYPES = {'piezas', 'pieza', 'pieces', 'piece'}

UNIT_ALIASES = {
    'LTR': 'LT',
    'L': 'LT',
    'FLOZ': 'OZ',
    'FL OZ': 'OZ',
    'GALON': 'GAL',
    'GALONES': 'GAL',
    'PK': 'CT',
    'PC': 'CT',
    'EA': 'CT',
}


def _normalize_unit_token(raw_unit):
    token = re.sub(r'\s+', ' ', (raw_unit or '').strip().upper())
    return UNIT_ALIASES.get(token, token.replace(' ', ''))


def _parse_slash_packaging_match(match):
    units_per_case = int(match.group(1))
    if units_per_case <= 0:
        return None

    size_value = match.group(2).strip().rstrip('.')
    unit_token = _normalize_unit_token(match.group(3))
    unit_size_label = f'{size_value} {unit_token}'

    return {
        'units_per_case': units_per_case,
        'unit_size_label': unit_size_label,
        'presentation_name': 'Caja',
        'content_type': unit_size_label,
    }


def _last_pattern_match(pattern, text):
    match = None
    for candidate in pattern.finditer(text):
        match = candidate
    return match


def parse_case_packaging_from_product_name(name):
    """
    Parse packaging embedded in a product name.

    Supports slash patterns like ``4/4.65 LT`` or ``24/100 CT``, and simple
    count suffixes like ``6 CT`` used on candles and similar products.
    """
    text = (name or '').strip()
    if not text:
        return None

    slash_match = _last_pattern_match(CASE_PACK_PATTERN, text)
    if slash_match is not None:
        return _parse_slash_packaging_match(slash_match)

    count_match = _last_pattern_match(SIMPLE_COUNT_PATTERN, text)
    if count_match is not None:
        units_per_case = int(count_match.group(1))
        if units_per_case <= 0:
            return None
        return {
            'units_per_case': units_per_case,
            'unit_size_label': f'{units_per_case} CT',
            'presentation_name': 'Caja',
            'content_type': 'piezas',
        }

    return None


def _normalize_content_token(value):
    normalized = unicodedata.normalize('NFKD', (value or '').strip().lower())
    normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9 ]', '', normalized).strip()


def content_type_looks_like_unit_size(value):
    text = (value or '').strip()
    if not text:
        return False
    if _normalize_content_token(text) in GENERIC_CONTENT_TYPES:
        return False
    return bool(SIZE_HINT_PATTERN.search(text))


def build_packaging_customer_description(*, units, content_type, presentation_name, language=None):
    from django.utils.translation import get_language

    units = max(int(units or 1), 1)
    content_type = (content_type or '').strip()
    presentation_name = (presentation_name or '').strip().lower()
    active_language = (language or get_language() or 'es').lower()
    english = active_language.startswith('en')

    if units > 1 and content_type_looks_like_unit_size(content_type):
        if english:
            return f'{units} units of size {content_type} per {presentation_name}'
        return f'{units} unidades de tamaño {content_type} por {presentation_name}'

    if units == 1 and content_type_looks_like_unit_size(content_type):
        if english:
            return f'1 pack of {content_type} per {presentation_name}'
        return f'1 paquete de {content_type} por {presentation_name}'

    if units > 1:
        content_lower = _normalize_content_token(content_type)
        if content_lower in COUNT_CONTENT_TYPES:
            label = 'pieces' if english else 'piezas'
        else:
            label = content_type.lower() if content_type else ('units' if english else 'unidades')
        if english:
            return f'{units} {label} per {presentation_name}'
        return f'{units} {label} por {presentation_name}'

    if content_type:
        if english:
            return f'1 {content_type.lower()} per {presentation_name}'
        return f'1 {content_type.lower()} por {presentation_name}'

    return presentation_name


def apply_case_packaging_defaults_to_presentacion(presentacion, product_name, *, overwrite=False):
    parsed = parse_case_packaging_from_product_name(product_name)
    if parsed is None:
        return False

    changed = False
    if overwrite or not (presentacion.nombre or '').strip() or (presentacion.nombre or '').strip().lower() in {'unit', 'unidad', 'units', 'unidades'}:
        presentacion.nombre = parsed['presentation_name']
        changed = True
    if overwrite or int(getattr(presentacion, 'unidades', 0) or 0) <= 1:
        presentacion.unidades = parsed['units_per_case']
        changed = True
    generic_content_types = {
        'unidades', 'unidad', 'units', 'unit',
        'piezas', 'pieza', 'pieces', 'piece',
        'caja', 'box',
    }
    if overwrite or not (presentacion.tipo_contenido or '').strip() or (presentacion.tipo_contenido or '').strip().lower() in generic_content_types:
        presentacion.tipo_contenido = parsed['content_type']
        changed = True
    return changed
