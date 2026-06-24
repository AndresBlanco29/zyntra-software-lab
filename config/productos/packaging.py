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

GENERIC_PRESENTATION_NAMES = {'unit', 'unidad', 'units', 'unidades', ''}
GENERIC_PRESENTATION_CONTENT = {
    'unit', 'unidad', 'units', 'unidades',
    'piezas', 'pieza', 'pieces', 'piece',
    'caja', 'cajas', 'box', 'boxes', '',
}

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

    text = re.sub(r'/+', '/', text)

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


def presentation_looks_unconfigured(presentacion):
    units = int(getattr(presentacion, 'unidades', 0) or 0)
    nombre = _normalize_content_token(getattr(presentacion, 'nombre', ''))
    tipo = _normalize_content_token(getattr(presentacion, 'tipo_contenido', ''))
    return units <= 1 and nombre in GENERIC_PRESENTATION_NAMES and tipo in GENERIC_PRESENTATION_CONTENT


def _localized_presentation_name(name, *, english):
    normalized = _normalize_content_token(name)
    if normalized in {'caja', 'cajas', 'box', 'boxes'}:
        return 'box' if english else 'caja'
    return (name or '').strip().lower()


def _localized_content_type_label(content_type, *, english):
    normalized = _normalize_content_token(content_type)
    if normalized in COUNT_CONTENT_TYPES:
        return 'pieces' if english else 'piezas'
    return (content_type or '').strip()


def get_effective_packaging_for_display(presentacion, *, language=None):
    from django.utils.translation import get_language

    active_language = (language or get_language() or 'en').lower()
    english = active_language.startswith('en')

    product = getattr(presentacion, 'producto', None)
    product_name = getattr(product, 'nombre', '') if product is not None else ''
    parsed = None
    if presentation_looks_unconfigured(presentacion) and product_name:
        parsed = parse_case_packaging_from_product_name(product_name)

    if parsed:
        units = parsed['units_per_case']
        content_type = _localized_content_type_label(parsed['content_type'], english=english)
        presentation_name = _localized_presentation_name(parsed['presentation_name'], english=english)
    else:
        units = presentacion.unidades
        content_type = presentacion.tipo_contenido_traducido
        presentation_name = presentacion.nombre_traducido

    description = build_packaging_customer_description(
        units=units,
        content_type=content_type,
        presentation_name=presentation_name,
        language=active_language,
    )
    return {
        'units': units,
        'content_type': content_type,
        'presentation_name': presentation_name,
        'description': description,
    }


def build_packaging_customer_description(*, units, content_type, presentation_name, language=None):
    from django.utils.translation import get_language

    units = max(int(units or 1), 1)
    content_type = (content_type or '').strip()
    presentation_name = (presentation_name or '').strip().lower()
    active_language = (language or get_language() or 'en').lower()
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


def finalize_quickbooks_import_packaging(*, product_name, presentation_name, tipo_contenido, unidades):
    """
    QuickBooks catalog imports should default to case/box packaging when the item
    does not expose an explicit sellable package (Unit, Each, etc.).
    """
    parsed = parse_case_packaging_from_product_name(product_name)
    presentation_token = _normalize_content_token(presentation_name)
    content_token = _normalize_content_token(tipo_contenido)
    generic_presentation_tokens = GENERIC_PRESENTATION_NAMES | {'ea', 'each'}
    generic_content_tokens = GENERIC_PRESENTATION_CONTENT | {'ea', 'each'}

    if parsed:
        if presentation_token in generic_presentation_tokens:
            presentation_name = parsed['presentation_name']
        if content_token in generic_content_tokens or int(unidades or 0) <= 1:
            tipo_contenido = parsed['content_type']
        if int(unidades or 0) <= 1:
            unidades = parsed['units_per_case']
    elif presentation_token in generic_presentation_tokens or content_token in generic_content_tokens:
        presentation_name = 'Caja'
        if content_token in generic_content_tokens:
            tipo_contenido = 'caja'
        if int(unidades or 0) <= 1:
            unidades = 1

    return {
        'presentation_name': (presentation_name or 'Caja').strip() or 'Caja',
        'tipo_contenido': (tipo_contenido or 'caja').strip() or 'caja',
        'unidades': max(int(unidades or 1), 1),
    }
