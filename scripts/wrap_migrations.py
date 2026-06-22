import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'config'
UTILS = 'config.core.migration_utils'
SKIP_PATTERNS = (
    'separate_add_fields',
    'wrap_add_field_operations',
    'SeparateDatabaseAndState',
    'add_model_fields_if_missing',
    'add_missing_',
)

APP_LABELS = {
    'clientes': 'clientes',
    'core': 'core',
    'cotizaciones': 'cotizaciones',
    'facturacion': 'facturacion',
    'integrations': 'integrations',
    'inventario': 'inventario',
    'notificaciones': 'notificaciones',
    'pedidos': 'pedidos',
    'productos': 'productos',
    'usuarios': 'usuarios',
}


def find_balanced_bracket(text, open_index):
    depth = 0
    index = open_index
    in_string = None
    escape = False

    while index < len(text):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == in_string:
                in_string = None
        else:
            if char in ('"', "'"):
                in_string = char
            elif char == '[':
                depth += 1
            elif char == ']':
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return None


def find_operations_block(text):
    match = re.search(r'\n([\t ]+)operations = \[', text)
    if not match:
        return None
    open_index = match.end() - 1
    close_index = find_balanced_bracket(text, open_index)
    if close_index is None:
        return None
    return match.start(), close_index + 1, match.group(1)


def insert_import(text):
    import_line = f'from {UTILS} import wrap_add_field_operations\n'
    if import_line in text:
        return text
    lines = text.splitlines(keepends=True)
    last_import_idx = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            last_import_idx = index
    lines.insert(last_import_idx + 1, import_line)
    return ''.join(lines)


updated = []
for path in sorted(ROOT.glob('*/migrations/*.py')):
    if path.name == '__init__.py':
        continue
    text = path.read_text(encoding='utf-8')
    if 'migrations.AddField' not in text:
        continue
    if any(pattern in text for pattern in SKIP_PATTERNS):
        continue

    app = path.parts[-3]
    app_label = APP_LABELS.get(app)
    if not app_label:
        continue

    block = find_operations_block(text)
    if block is None:
        print('NO MATCH', path)
        continue

    start, end, indent = block
    ops_body = text[text.find('[', start):end]
    ops_body = ops_body[1:-1]
    if 'migrations.AddField' not in ops_body:
        continue

    text = insert_import(text)
    block = find_operations_block(text)
    start, end, indent = block
    ops_body = text[text.find('[', start):end]
    ops_body = ops_body[1:-1]

    new_ops = f"\n{indent}operations = wrap_add_field_operations('{app_label}', [{ops_body}\n{indent}])\n"
    text = text[:start] + new_ops + text[end:]
    path.write_text(text, encoding='utf-8')
    updated.append(str(path))

print('Updated', len(updated), 'files:')
for item in updated:
    print(' ', item)
