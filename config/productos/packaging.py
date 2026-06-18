import re

CASE_PACK_PATTERN = re.compile(
    r'(\d+)\s*/\s*([\d.]+)\s*(LT|LTR|L|ML|OZ|FLOZ|FL\s*OZ|GAL|KG|LB|GR|G)\b',
    re.IGNORECASE,
)

UNIT_ALIASES = {
    'LTR': 'LT',
    'L': 'LT',
    'FLOZ': 'OZ',
    'FL OZ': 'OZ',
}


def _normalize_unit_token(raw_unit):
    token = re.sub(r'\s+', ' ', (raw_unit or '').strip().upper())
    return UNIT_ALIASES.get(token, token.replace(' ', ''))


def parse_case_packaging_from_product_name(name):
    """
    Parse patterns like ``4/4.65 LT`` or ``12/1 LT`` embedded in a product name.

    Returns a dict with ``units_per_case``, ``unit_size_label``, ``presentation_name``,
    and ``content_type`` when a match is found; otherwise ``None``.
    """
    text = (name or '').strip()
    if not text:
        return None

    match = None
    for candidate in CASE_PACK_PATTERN.finditer(text):
        match = candidate
    if match is None:
        return None

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
    if overwrite or not (presentacion.tipo_contenido or '').strip() or (presentacion.tipo_contenido or '').strip().lower() in {'unidades', 'unidad', 'units', 'unit', 'caja', 'box'}:
        presentacion.tipo_contenido = parsed['content_type']
        changed = True
    return changed
