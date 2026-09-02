"""Fill empty Spanish msgstr entries in locale/es/LC_MESSAGES/django.po."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ES_PO = ROOT / 'config' / 'locale' / 'es' / 'LC_MESSAGES' / 'django.po'
EN_PO = ROOT / 'config' / 'locale' / 'en' / 'LC_MESSAGES' / 'django.po'

# English msgid -> Spanish msgstr for newer UI strings not covered by the legacy catalog.
MANUAL_EN_TO_ES = {
    'Sales Orders': 'Órdenes de venta',
    'Received Sales Orders': 'Órdenes de venta recibidas',
    'Pending Orders': 'Órdenes pendientes',
    'Orders in progress': 'Órdenes en progreso',
    'Completed Orders': 'Órdenes completadas',
    'Cancelled Orders': 'Órdenes canceladas',
    'Showing %(start)s-%(end)s of %(total)s orders': 'Mostrando %(start)s-%(end)s de %(total)s órdenes',
    'Sales order pagination': 'Paginación de órdenes de venta',
    'Manage': 'Gestionar',
    'Selector': 'Seleccionador',
    'Origin': 'Origen',
    'Warehouse check': 'Verificación en bodega',
    'Received date': 'Fecha de recibo',
    'This username is already in use by %(owner)s (login: %(username)s). Search that customer and change its username first, or pick a different username.': (
        'Este usuario ya está en uso por %(owner)s (login: %(username)s). '
        'Busca ese cliente y cámbiale el usuario primero, o elige otro usuario.'
    ),
    'Picking Ticket': 'Ticket de picking',
    'Export PDF': 'Exportar PDF',
    'Generate Picking Ticket': 'Generar ticket de picking',
    'Previous': 'Anterior',
    'Next': 'Siguiente',
    'Page %(current)s of %(total)s': 'Página %(current)s de %(total)s',
    'Product pagination': 'Paginación de productos',
    'Showing %(start)s-%(end)s of %(total)s products': 'Mostrando %(start)s-%(end)s de %(total)s productos',
    'Live Drivers': 'Conductores en vivo',
    'Monitor every driver currently on route and watch their vehicle move in real time.': (
        'Monitorea cada conductor actualmente en ruta y observa su vehículo moverse en tiempo real.'
    ),
    'Active drivers': 'Conductores activos',
    'Automatic refresh every 10 seconds': 'Actualización automática cada 10 segundos',
    'Invoices': 'Facturas',
    'Generated from verified picking quantities.': 'Generadas a partir de cantidades verificadas en picking.',
    'Delivery method': 'Método de entrega',
    'Dispatch': 'Despacho',
    'Operational inventory': 'Inventario operativo',
    'Review physical, reserved, and available stock by presentation.': (
        'Revisa el stock físico, reservado y disponible por presentación.'
    ),
    'Search product or presentation': 'Buscar producto o presentación',
    'Physical stock': 'Stock físico',
    'Reserved stock': 'Stock reservado',
    'Out of stock': 'Sin stock',
    'Inventory': 'Inventario',
    'Assigned Picking Tickets': 'Tickets de picking asignados',
    'Pending Picking Tickets': 'Tickets de picking pendientes',
    'Processed Picking Tickets': 'Tickets de picking procesados',
    'Picking Verification': 'Verificación de picking',
    'Real quantities by product': 'Cantidades reales por producto',
    'Add product': 'Agregar producto',
    'Back': 'Volver',
    'Customer note': 'Nota del cliente',
    'Presentation': 'Presentación',
    'Quantity': 'Cantidad',
    'Product': 'Producto',
    'Status': 'Estado',
    'Customer': 'Cliente',
    'Contact': 'Contacto',
    'Date': 'Fecha',
    'Total': 'Total',
    'Action': 'Acción',
    'Close': 'Cerrar',
    'Search shortcuts, modules, or pages': 'Buscar accesos directos, módulos o páginas',
    'Log out': 'Cerrar sesión',
    'My Profile': 'Mi perfil',
    'Vendor menu': 'Menú del vendedor',
    'What do you want to do?': '¿Qué quieres hacer?',
    'Choose an action. Everything for your sales route is here.': (
        'Elige una acción. Aquí está todo lo de tu ruta de ventas.'
    ),
    'Credit Memo': 'Nota de crédito',
    'Return': 'Devolución',
    'My Notes': 'Mis notas',
    'Credit the customer without returning stock to inventory.': (
        'Acredita al cliente sin devolver stock al inventario.'
    ),
    'Credit the customer and return product to inventory after approval.': (
        'Acredita al cliente y devuelve producto al inventario después de la aprobación.'
    ),
    'Review credit memos and returns you already submitted.': (
        'Revisa las notas de crédito y devoluciones que ya enviaste.'
    ),
    'Build and submit an order for an assigned customer.': (
        'Arma y envía un pedido para un cliente asignado.'
    ),
    'See your assigned customers, balances, and account details.': (
        'Mira tus clientes asignados, saldos y detalles de cuenta.'
    ),
    'Register a new customer for your route.': 'Registra un cliente nuevo para tu ruta.',
    'Submit credit memo draft': 'Enviar borrador de nota de crédito',
    'Submit return draft': 'Enviar borrador de devolución',
    'Search product': 'Buscar producto',
    'Search products by full name...': 'Busca productos por nombre completo...',
    'Type at least 2 characters to search.': 'Escribe al menos 2 caracteres para buscar.',
    'No products found.': 'No se encontraron productos.',
    'Name': 'Nombre',
    'Company Name': 'Nombre de empresa',
    'Email': 'Correo electrónico',
    'Type to search...': 'Escribe para buscar...',
    'No results found': 'No se encontraron resultados',
    'Catalog-only mode': 'Modo solo catálogo',
    'Open review queue': 'Abrir cola de revisión',
    'Run pull sync now': 'Ejecutar sincronización incremental',
    'Run full resync': 'Ejecutar resincronización completa',
    'Keep both systems aligned': 'Mantener ambos sistemas alineados',
    'Orders Received': 'Pedidos recibidos',
    'My Order': 'Mi pedido',
    'Add to Order': 'Añadir al pedido',
    'Packaging': 'Presentación',
    'Your price': 'Tu precio',
    'Search': 'Buscar',
    'Cancel': 'Cancelar',
    'Save': 'Guardar',
    'Delete': 'Eliminar',
    'Edit': 'Editar',
    'Create': 'Crear',
    'Update': 'Actualizar',
    'Yes': 'Sí',
    'No': 'No',
    'Loading...': 'Cargando...',
    'Pending verification': 'Pendiente de verificación',
    'Invoice generated': 'Factura generada',
    'No sales orders have been registered yet.': 'Aún no se han registrado órdenes de venta.',
    'Authorize selling below cost': 'Autorizar venta a pérdida',
    'Allow saving orders or generating invoices when a product is sold below cost. Requires naming who authorized the exception.': (
        'Permite guardar órdenes o generar facturas cuando un producto se vende bajo costo. '
        'Requiere indicar quién autorizó la excepción.'
    ),
    'Selling below cost — authorization required': 'Venta a pérdida — se requiere autorización',
    'Selling below cost — authorized exception': 'Venta a pérdida — excepción autorizada',
    'One or more products are priced below cost. Saving changes or generating an invoice requires supervisor authorization.': (
        'Uno o más productos están por debajo del costo. Guardar cambios o generar la factura requiere autorización de un supervisor.'
    ),
    'Selling': 'Venta',
    'cost': 'costo',
    'Loss': 'Pérdida',
    'Ask a supervisor with permission to authorize selling below cost before saving or invoicing.': (
        'Pide a un supervisor con permiso que autorice la venta a pérdida antes de guardar o facturar.'
    ),
    'I authorize saving this order with products sold below cost': (
        'Autorizo guardar esta orden con productos vendidos a pérdida'
    ),
    'I authorize generating this invoice with products sold below cost': (
        'Autorizo generar esta factura con productos vendidos a pérdida'
    ),
    'Full name of the person authorizing this exception': 'Nombre completo de quien autoriza esta excepción',
    'Reason for selling below cost': 'Motivo de la venta a pérdida',
    'Required only when one or more products are sold below cost. The action is recorded in the Audit Trail.': (
        'Solo es necesario cuando uno o más productos se venden a pérdida. La acción queda registrada en el historial de auditoría.'
    ),
    'Authorize selling below cost above before generating the invoice.': (
        'Autoriza la venta a pérdida arriba antes de generar la factura.'
    ),
    'Invoice generation is blocked because one or more products are sold below cost.': (
        'La generación de factura está bloqueada porque uno o más productos se venden a pérdida.'
    ),
    'Cannot continue: one or more products are being sold below cost. A supervisor authorization is required. %(details)s': (
        'No se puede continuar: uno o más productos se están vendiendo a pérdida. '
        'Se requiere autorización de un supervisor. %(details)s'
    ),
    '%(product)s: selling $%(net)s (cost $%(cost)s)': '%(product)s: se vende a $%(net)s (costo $%(cost)s)',
    'Enter who authorized selling below cost.': 'Indica quién autorizó la venta a pérdida.',
    'You are not allowed to authorize selling below cost.': 'No tienes permiso para autorizar la venta a pérdida.',
    'Authorized selling below cost on order #%(id)s': 'Autorizó venta a pérdida en la orden #%(id)s',
    'Sell below cost': 'Venta a pérdida',
    'Blocked': 'Bloqueado',
    'Authorized': 'Autorizado',
    'Photo': 'Foto',
    'Authorized by': 'Autorizado por',
    'Comment': 'Comentario',
    'Comment / observation (optional)': 'Comentario / observación (opcional)',
    'Selling below cost — pending authorization': 'Venta a pérdida — autorización pendiente',
    'One or more products are priced below cost. A supervisor already authorized this exception.': (
        'Uno o más productos están por debajo del costo. Un supervisor ya autorizó esta excepción.'
    ),
    'One or more products are priced below cost. Your order changes are saved; generating an invoice still requires supervisor authorization.': (
        'Uno o más productos están por debajo del costo. Los cambios de la orden ya están guardados; '
        'generar la factura aún requiere autorización de un supervisor.'
    ),
    'Authorize now': 'Autorizar ahora',
    'Approves the exception without re-saving the full order grid. The action is recorded in the Audit Trail.': (
        'Aprueba la excepción sin volver a guardar toda la grilla de la orden. La acción queda registrada en el historial de auditoría.'
    ),
    'Ask a supervisor with permission to authorize selling below cost before generating an invoice. Your order edits remain saved.': (
        'Pide a un supervisor con permiso que autorice la venta a pérdida antes de generar la factura. '
        'Tus ediciones de la orden permanecen guardadas.'
    ),
    'Order saved. One or more products are sold below cost and require supervisor authorization before generating an invoice.': (
        'Orden guardada. Uno o más productos se venden a pérdida y requieren autorización de un supervisor antes de generar la factura.'
    ),
    'Selling below cost was authorized. You can continue to invoice when ready.': (
        'La venta a pérdida quedó autorizada. Puedes continuar a facturar cuando estés listo.'
    ),
    'This order no longer has products sold below cost.': 'Esta orden ya no tiene productos vendidos a pérdida.',
    'Pending authorization': 'Autorización pendiente',
    'Optional when saving: authorize in the same save if you already have supervisor approval. Otherwise save first, then use Authorize now.': (
        'Opcional al guardar: autoriza en el mismo guardado si ya tienes aprobación de supervisor. '
        'Si no, guarda primero y luego usa Autorizar ahora.'
    ),
    'Authorize selling below cost with Authorize now (or in the invoice form) before generating the invoice. Order edits are already saved.': (
        'Autoriza la venta a pérdida con Autorizar ahora (o en el formulario de factura) antes de generar la factura. '
        'Las ediciones de la orden ya están guardadas.'
    ),
    'Invoice generation is blocked because one or more products are sold below cost. Order edits remain saved until a supervisor authorizes the exception.': (
        'La generación de factura está bloqueada porque uno o más productos se venden a pérdida. '
        'Las ediciones de la orden permanecen guardadas hasta que un supervisor autorice la excepción.'
    ),
}


def _unquote_po_string(block: str) -> str:
    if block == '""':
        return ''
    parts = re.findall(r'"((?:\\.|[^"\\])*)"', block)
    return ''.join(parts).replace('\\n', '\n').replace('\\"', '"')


def _read_po_entries(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding='utf-8')
    entries: list[tuple[str, str]] = []
    pattern = re.compile(
        r'msgid\s+((?:"(?:\\.|[^"\\])*"(?:\s+"(?:\\.|[^"\\])*")*)|"")\s*'
        r'msgstr\s+((?:"(?:\\.|[^"\\])*"(?:\s+"(?:\\.|[^"\\])*")*)|"")',
        re.MULTILINE,
    )

    for msgid_block, msgstr_block in pattern.findall(text):
        entries.append((_unquote_po_string(msgid_block), _unquote_po_string(msgstr_block)))
    return entries


def _quote_po(value: str) -> str:
    if not value:
        return 'msgstr ""'
    if '\n' in value:
        lines = value.split('\n')
        return 'msgstr ""\n' + '\n'.join(f'"{line}\\n"' for line in lines[:-1]) + (
            f'\n"{lines[-1]}"' if lines[-1] else ''
        )
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return f'msgstr "{escaped}"'


def _build_reverse_en_map() -> dict[str, str]:
    reverse: dict[str, str] = {}
    spanish_msgids: set[str] = set()
    for msgid, msgstr in _read_po_entries(EN_PO):
        if msgid and msgstr:
            reverse[msgstr] = msgid
            spanish_msgids.add(msgid)
    reverse.update({en: es for en, es in MANUAL_EN_TO_ES.items()})
    return reverse, spanish_msgids


def _looks_spanish(text: str) -> bool:
    if any(ch in text for ch in 'áéíóúñÁÉÍÓÚÑ¿¡'):
        return True
    spanish_markers = (
        'ción', 'ación', 'iente', 'ección', 'ólogo', 'usuario', 'contraseña',
        'empresa', 'producto', 'pedido', 'factura', 'cliente', 'bodega',
    )
    lowered = text.lower()
    return any(marker in lowered for marker in spanish_markers)


def fill_spanish_catalog() -> tuple[int, int]:
    reverse, spanish_msgids = _build_reverse_en_map()
    app_translations: dict[str, str] = {}
    app_translations_path = ROOT / 'config' / 'locale' / 'es' / 'app_translations.json'
    complete_translations_path = ROOT / 'config' / 'locale' / 'es' / 'complete_en_to_es.json'
    if app_translations_path.exists():
        import json

        app_translations = json.loads(app_translations_path.read_text(encoding='utf-8'))
    if complete_translations_path.exists():
        import json

        app_translations.update(json.loads(complete_translations_path.read_text(encoding='utf-8')))
    content = ES_PO.read_text(encoding='utf-8')
    filled = 0
    skipped = 0

    def replacer(match: re.Match[str]) -> str:
        nonlocal filled, skipped
        msgid_block = match.group(1)
        msgstr_block = match.group(2)
        msgid = _unquote_po_string(msgid_block)
        current = _unquote_po_string(msgstr_block)
        if not msgid:
            return match.group(0)

        translation = reverse.get(msgid) or app_translations.get(msgid)
        if not translation and msgid in spanish_msgids:
            translation = msgid
        if not translation and _looks_spanish(msgid):
            translation = msgid
        if not translation:
            skipped += 1
            return match.group(0)

        if current.strip() and current != msgid and current != translation:
            return match.group(0)

        filled += 1
        return f'msgid {msgid_block}\n{_quote_po(translation)}'

    updated = re.sub(
        r'msgid\s+((?:"(?:\\.|[^"\\])*"(?:\s+"(?:\\.|[^"\\])*")*)|"")\s*'
        r'msgstr\s+((?:"(?:\\.|[^"\\])*"(?:\s+"(?:\\.|[^"\\])*")*)|"")',
        replacer,
        content,
    )

    header = updated.replace('"Language: \\n"', '"Language: es\\n"')
    ES_PO.write_text(header, encoding='utf-8')
    return filled, skipped


if __name__ == '__main__':
    filled_count, skipped_count = fill_spanish_catalog()
    print(f'Filled {filled_count} entries; {skipped_count} still untranslated.')
