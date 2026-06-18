"""Generate complete_en_to_es.json for app-facing English msgids."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config.core.fill_spanish_catalog import MANUAL_EN_TO_ES, _build_reverse_en_map, _looks_spanish

MSGIDS = Path(__file__).with_name('app_msgids.json')
OUTPUT = ROOT / 'config' / 'locale' / 'es' / 'complete_en_to_es.json'

EXACT = {
    **MANUAL_EN_TO_ES,
    'Operational Inventory': 'Inventario operativo',
    'Review physical, reserved and available stock per presentation.': (
        'Revisa el stock físico, reservado y disponible por presentación.'
    ),
    'Active drivers': 'Conductores activos',
    'Auto refresh every 10 seconds': 'Actualización automática cada 10 segundos',
    'Automatic refresh every 10 seconds': 'Actualización automática cada 10 segundos',
    'Sign in': 'Iniciar sesión',
    "Don't have an account?": '¿No tienes cuenta?',
    'Register': 'Registrarse',
    'Dispatch': 'Despacho',
    'ID': 'ID',
    'ACH': 'ACH',
    'AM': 'AM',
    'PM': 'PM',
    'U/M': 'U/M',
    'QTY ORD': 'CANT. SOL.',
    'QTY PICK': 'CANT. PICK',
    'QTY DSP': 'CANT. DSP.',
    'PHONE': 'TELÉFONO',
}

PHRASES = [
    ('A simpler place to manage QuickBooks', 'Un lugar más simple para administrar QuickBooks'),
    ('Accounting sync workspace', 'Espacio de sincronización contable'),
    ('Access to administrative areas and supervisory workflows.', 'Acceso a áreas administrativas y flujos de supervisión.'),
    ('A cheque image is required for cheque payments.', 'Se requiere una imagen del cheque para pagos con cheque.'),
    ('A credit type is required for credit notes.', 'Se requiere un tipo de crédito para las notas de crédito.'),
    ('A driver is required for route deliveries.', 'Se requiere un conductor para entregas en ruta.'),
    ('A maximum of three payment methods is allowed per delivery.', 'Se permiten máximo tres métodos de pago por entrega.'),
    ('A new order request has been received.', 'Se recibió una nueva solicitud de pedido.'),
    ('A payment method is required when the delivery is paid.', 'Se requiere un método de pago cuando la entrega está pagada.'),
    ('A picking note is required when physical stock is insufficient.', 'Se requiere una nota de picking cuando el stock físico es insuficiente.'),
    ('A reason is required when the customer does not pay.', 'Se requiere una razón cuando el cliente no paga.'),
    ('A selector must be assigned before verification starts.', 'Debe asignarse un seleccionador antes de iniciar la verificación.'),
    ('Add a new product to this order', 'Agregar un producto nuevo a esta orden'),
    ('Add evidence', 'Agregar evidencia'),
    ('Added by picker', 'Agregado por el seleccionador'),
    ('Additional note', 'Nota adicional'),
    ('Address', 'Dirección'),
    ('Address is required.', 'La dirección es obligatoria.'),
    ('Adjustment notes', 'Notas de ajuste'),
    ('Administration', 'Administración'),
    ('All customers', 'Todos los clientes'),
    ('All drivers', 'Todos los conductores'),
    ('All statuses', 'Todos los estados'),
    ('Amount', 'Monto'),
    ('Amount received', 'Monto recibido'),
    ('BackOffice', 'BackOffice'),
    ('Cancel order', 'Cancelar orden'),
    ('Completed', 'Completado'),
    ('Confirm delivery', 'Confirmar entrega'),
    ('Create direct invoice', 'Crear factura directa'),
    ('Customer requests', 'Solicitudes de clientes'),
    ('Database Backups', 'Respaldos de base de datos'),
    ('Delivery method', 'Método de entrega'),
    ('Direct invoice', 'Factura directa'),
    ('Export PDF', 'Exportar PDF'),
    ('Fulfillment', 'Despacho'),
    ('Generate invoice', 'Generar factura'),
    ('Inventory movements', 'Movimientos de inventario'),
    ('Live tracking', 'Seguimiento en vivo'),
    ('Manage sales orders', 'Gestionar órdenes de venta'),
    ('My Profile', 'Mi perfil'),
    ('No results found', 'No se encontraron resultados'),
    ('Open review queue', 'Abrir cola de revisión'),
    ('Orders Received', 'Pedidos recibidos'),
    ('Pending dispatch', 'Pendiente de despacho'),
    ('QuickBooks Center', 'Centro de QuickBooks'),
    ('Reports Center', 'Centro de reportes'),
    ('Review queue', 'Cola de revisión'),
    ('Sales Orders', 'Órdenes de venta'),
    ('Search shortcuts, modules, or pages', 'Buscar accesos directos, módulos o páginas'),
    ('Send picking', 'Enviar picking'),
    ('Type to search...', 'Escribe para buscar...'),
    ('Warehouse check', 'Verificación en bodega'),
]

TOKEN_REPLACEMENTS = [
    ('Customers', 'Clientes'),
    ('Customer', 'Cliente'),
    ('Invoices', 'Facturas'),
    ('Invoice', 'Factura'),
    ('Orders', 'Órdenes'),
    ('Order', 'Orden'),
    ('Products', 'Productos'),
    ('Product', 'Producto'),
    ('Drivers', 'Conductores'),
    ('Driver', 'Conductor'),
    ('Inventory', 'Inventario'),
    ('Delivery', 'Entrega'),
    ('Deliveries', 'Entregas'),
    ('Payment', 'Pago'),
    ('Payments', 'Pagos'),
    ('Amount', 'Monto'),
    ('Quantity', 'Cantidad'),
    ('Presentation', 'Presentación'),
    ('Selector', 'Seleccionador'),
    ('Picking', 'Picking'),
    ('BackOffice', 'BackOffice'),
    ('QuickBooks', 'QuickBooks'),
    ('Search', 'Buscar'),
    ('Save', 'Guardar'),
    ('Cancel', 'Cancelar'),
    ('Delete', 'Eliminar'),
    ('Edit', 'Editar'),
    ('Create', 'Crear'),
    ('Update', 'Actualizar'),
    ('Manage', 'Gestionar'),
    ('Pending', 'Pendiente'),
    ('Completed', 'Completado'),
    ('Cancelled', 'Cancelado'),
    ('Active', 'Activo'),
    ('Status', 'Estado'),
    ('Total', 'Total'),
    ('Date', 'Fecha'),
    ('Notes', 'Notas'),
    ('Note', 'Nota'),
    ('Required', 'Obligatorio'),
    ('required', 'obligatorio'),
    (' successfully', ' correctamente'),
    ('Successfully', 'Correctamente'),
    ('Add ', 'Agregar '),
    (' added ', ' agregado '),
    (' updated ', ' actualizado '),
    (' created ', ' creado '),
    (' saved ', ' guardado '),
    (' cannot ', ' no puede '),
    (' must ', ' debe '),
    (' before ', ' antes de '),
    (' after ', ' después de '),
    (' during ', ' durante '),
    (' for ', ' para '),
    (' with ', ' con '),
    (' without ', ' sin '),
    (' and ', ' y '),
    (' or ', ' o '),
    (' the ', ' el '),
    ('The ', 'El '),
    (' a ', ' un '),
    ('A ', 'Un '),
    (' an ', ' un '),
    (' is ', ' es '),
    (' are ', ' son '),
    (' was ', ' fue '),
    (' were ', ' fueron '),
    (' to ', ' a '),
    (' from ', ' de '),
    (' in ', ' en '),
    (' on ', ' en '),
    (' at ', ' en '),
    (' by ', ' por '),
    (' all ', ' todos '),
    (' All ', ' Todos '),
    (' new ', ' nuevo '),
    (' New ', ' Nuevo '),
    (' open ', ' abierto '),
    (' Open ', ' Abrir '),
    (' review ', ' revisar '),
    (' Review ', ' Revisar '),
    (' queue ', ' cola '),
    (' stock ', ' stock '),
    (' route ', ' ruta '),
    (' Route ', ' Ruta '),
    (' evidence ', ' evidencia '),
    (' adjustment ', ' ajuste '),
    (' Adjustment ', ' Ajuste '),
    (' credit ', ' crédito '),
    (' Credit ', ' Crédito '),
    (' debit ', ' débito '),
    (' Debit ', ' Débito '),
    (' vendor ', ' vendedor '),
    (' Vendor ', ' Vendedor '),
    (' supplier ', ' proveedor '),
    (' Supplier ', ' Proveedor '),
    (' report ', ' reporte '),
    (' Report ', ' Reporte '),
    (' reports ', ' reportes '),
    (' Reports ', ' Reportes '),
    (' backup ', ' respaldo '),
    (' Backup ', ' Respaldo '),
    (' restore ', ' restaurar '),
    (' Restore ', ' Restaurar '),
    (' assigned ', ' asignado '),
    (' Assigned ', ' Asignado '),
    (' verification ', ' verificación '),
    (' Verification ', ' Verificación '),
    (' confirmed ', ' confirmado '),
    (' Confirmed ', ' Confirmado '),
    (' received ', ' recibido '),
    (' Received ', ' Recibido '),
    (' generated ', ' generado '),
    (' Generated ', ' Generado '),
    (' exported ', ' exportado '),
    (' Exported ', ' Exportado '),
    (' imported ', ' importado '),
    (' Imported ', ' Importado '),
    (' synced ', ' sincronizado '),
    (' Synced ', ' Sincronizado '),
    (' sync ', ' sincronización '),
    (' Sync ', ' Sincronizar '),
    (' conflict ', ' conflicto '),
    (' Conflict ', ' Conflicto '),
    (' catalog ', ' catálogo '),
    (' Catalog ', ' Catálogo '),
    (' warehouse ', ' bodega '),
    (' Warehouse ', ' Bodega '),
    (' physical ', ' físico '),
    (' Physical ', ' Físico '),
    (' reserved ', ' reservado '),
    (' Reserved ', ' Reservado '),
    (' available ', ' disponible '),
    (' Available ', ' Disponible '),
    (' overdue ', ' vencido '),
    (' Overdue ', ' Vencido '),
    (' paid ', ' pagado '),
    (' Paid ', ' Pagado '),
    (' unpaid ', ' impago '),
    (' Unpaid ', ' Impago '),
    (' dispatched ', ' despachado '),
    (' Dispatched ', ' Despachado '),
    (' delivered ', ' entregado '),
    (' Delivered ', ' Entregado '),
    (' cancelled ', ' cancelado '),
    (' Cancelled ', ' Cancelado '),
    (' approved ', ' aprobado '),
    (' Approved ', ' Aprobado '),
    (' rejected ', ' rechazado '),
    (' Rejected ', ' Rechazado '),
    (' draft ', ' borrador '),
    (' Draft ', ' Borrador '),
    (' live ', ' en vivo '),
    (' Live ', ' En vivo '),
    (' tracking ', ' seguimiento '),
    (' Tracking ', ' Seguimiento '),
    (' profile ', ' perfil '),
    (' Profile ', ' Perfil '),
    (' settings ', ' configuración '),
    (' Settings ', ' Configuración '),
    (' users ', ' usuarios '),
    (' Users ', ' Usuarios '),
    (' permissions ', ' permisos '),
    (' Permissions ', ' Permisos '),
    (' role ', ' rol '),
    (' Role ', ' Rol '),
    (' roles ', ' roles '),
    (' Roles ', ' Roles '),
    (' company ', ' empresa '),
    (' Company ', ' Empresa '),
    (' contact ', ' contacto '),
    (' Contact ', ' Contacto '),
    (' address ', ' dirección '),
    (' Address ', ' Dirección '),
    (' phone ', ' teléfono '),
    (' Phone ', 'Teléfono'),
    (' email ', ' correo '),
    (' Email ', 'Correo electrónico'),
    (' password ', ' contraseña '),
    (' Password ', 'Contraseña'),
    (' username ', ' usuario '),
    (' Username ', 'Usuario'),
    (' login ', ' inicio de sesión '),
    (' Login ', 'Iniciar sesión'),
    (' logout ', ' cerrar sesión '),
    (' Log out', 'Cerrar sesión'),
    (' register ', ' registrarse '),
    (' Register ', 'Registrarse'),
    (' submit ', ' enviar '),
    (' Submit ', 'Enviar'),
    (' confirm ', ' confirmar '),
    (' Confirm ', 'Confirmar'),
    (' continue ', ' continuar '),
    (' Continue ', 'Continuar'),
    (' back ', ' volver '),
    (' Back', 'Volver'),
    (' next ', ' siguiente '),
    (' Next', 'Siguiente'),
    (' previous ', ' anterior '),
    (' Previous', 'Anterior'),
    (' page ', ' página '),
    (' Page ', 'Página '),
    (' showing ', ' mostrando '),
    (' Showing ', 'Mostrando '),
    (' of ', ' de '),
    (' results ', ' resultados '),
    (' Results ', 'Resultados'),
    (' loading ', ' cargando '),
    (' Loading...', 'Cargando...'),
    (' error ', ' error '),
    (' Error ', 'Error'),
    (' warning ', ' advertencia '),
    (' Warning ', 'Advertencia'),
    (' success ', ' éxito '),
    (' Success ', 'Éxito'),
    (' failed ', ' falló '),
    (' Failed ', 'Falló'),
    (' completed ', ' completado '),
    (' Completed ', 'Completado'),
    (' processing ', ' procesando '),
    (' Processing', 'Procesando'),
    (' please wait', ' por favor espera'),
    (' Please wait', 'Por favor espera'),
]

PLACEHOLDER_PATTERN = re.compile(r'(%\([\w]+\)s|%\([\w]+\)d|%\([\w]+\)\.?\d*f|%\([\w]+\)[^)]+\)|\{[^}]+\})')


def _protect_placeholders(text: str) -> tuple[str, list[str]]:
    placeholders: list[str] = []

    def repl(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f'__PH_{len(placeholders)-1}__'

    return PLACEHOLDER_PATTERN.sub(repl, text), placeholders


def _restore_placeholders(text: str, placeholders: list[str]) -> str:
    for index, value in enumerate(placeholders):
        text = text.replace(f'__PH_{index}__', value)
    return text


def rough_translate(text: str) -> str:
    if not text or _looks_spanish(text):
        return text
    if text in EXACT:
        return EXACT[text]
    for en, es in PHRASES:
        if text == en:
            return es
    protected, placeholders = _protect_placeholders(text)
    translated = protected
    for src, dst in TOKEN_REPLACEMENTS:
        translated = translated.replace(src, dst)
    translated = _restore_placeholders(translated, placeholders)
    if translated != text:
        return translated
    return text


def main() -> None:
    reverse, spanish_msgids = _build_reverse_en_map()
    msgids = json.loads(MSGIDS.read_text(encoding='utf-8'))
    translations: dict[str, str] = {}

    for msgid in msgids:
        if not msgid:
            continue
        if msgid in EXACT:
            translations[msgid] = EXACT[msgid]
        elif msgid in spanish_msgids or _looks_spanish(msgid):
            translations[msgid] = msgid
        elif msgid in reverse:
            translations[msgid] = reverse[msgid]
        else:
            translations[msgid] = rough_translate(msgid)

    OUTPUT.write_text(json.dumps(translations, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    print(f'Wrote {len(translations)} translations to {OUTPUT}')


if __name__ == '__main__':
    main()
