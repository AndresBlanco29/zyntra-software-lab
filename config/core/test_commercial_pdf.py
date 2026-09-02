from decimal import Decimal
from unittest.mock import MagicMock

from django.test import SimpleTestCase, override_settings
from django.utils.translation import activate

from config.core.commercial_pdf import (
    build_order_line_rows,
    build_quote_line_rows,
    quote_pdf_response,
    order_pdf_response,
)


def _fake_presentacion(name='Demo Product', pack='Case'):
    producto = MagicMock()
    producto.nombre = name
    presentacion = MagicMock()
    presentacion.producto = producto
    presentacion.nombre = pack
    return presentacion


class CommercialPdfTests(SimpleTestCase):
    def setUp(self):
        activate('en')

    def test_quote_lines_mark_free_gifts_and_total(self):
        gift = MagicMock()
        gift.cantidad = 2
        gift.precio = Decimal('10.00')
        gift.subtotal = Decimal('0.00')
        gift.es_regalo = True
        gift.descuento_aplicado = False
        gift.descuento_monto = Decimal('0')
        gift.descuento_linea_total = Decimal('0')
        gift.precio_unitario_neto = Decimal('0')
        gift.presentacion = _fake_presentacion('Gift Flour')

        paid = MagicMock()
        paid.cantidad = 3
        paid.precio = Decimal('5.00')
        paid.subtotal = Decimal('15.00')
        paid.es_regalo = False
        paid.descuento_aplicado = False
        paid.descuento_monto = Decimal('0')
        paid.descuento_linea_total = Decimal('0')
        paid.precio_unitario_neto = Decimal('5.00')
        paid.presentacion = _fake_presentacion('Rice')

        cotizacion = MagicMock()
        cotizacion.total = Decimal('15.00')
        cotizacion.items.select_related.return_value.order_by.return_value = [gift, paid]

        rows, total = build_quote_line_rows(cotizacion)
        self.assertEqual(total, Decimal('15.00'))
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]['is_gift'])
        self.assertIn('FREE', rows[0]['name'])
        self.assertEqual(rows[0]['line_total'], Decimal('0.00'))
        self.assertEqual(rows[1]['line_total'], Decimal('15.00'))

    def test_order_lines_use_requested_qty(self):
        item = MagicMock()
        item.cantidad_solicitada = 4
        item.cantidad = 2
        item.precio = Decimal('8.00')
        item.subtotal = Decimal('32.00')
        item.es_regalo = False
        item.descuento_linea_total = Decimal('0')
        item.precio_unitario_neto = Decimal('8.00')
        item.presentacion = _fake_presentacion('Beans')

        pedido = MagicMock()
        pedido.total = Decimal('32.00')
        pedido.items.select_related.return_value.order_by.return_value = [item]

        rows, total = build_order_line_rows(pedido)
        self.assertEqual(rows[0]['qty'], 4)
        self.assertEqual(total, Decimal('32.00'))

    @override_settings(DEMO_MODE=True, DEMO_BRAND_LEGAL_NAME='Zyntra')
    def test_quote_pdf_filename_and_no_tortilla_brand(self):
        from config.core import pdf_branding

        pdf_branding._apply_demo_brand_palette()

        item = MagicMock()
        item.cantidad = 1
        item.precio = Decimal('10.00')
        item.subtotal = Decimal('10.00')
        item.es_regalo = False
        item.descuento_aplicado = False
        item.descuento_monto = Decimal('0')
        item.descuento_linea_total = Decimal('0')
        item.precio_unitario_neto = Decimal('10.00')
        item.presentacion = _fake_presentacion()

        cliente = MagicMock()
        cliente.nombre_empresa = 'Harborline Market Group'
        cliente.direccion = '1 Demo St'
        cliente.ciudad = 'Austin'
        cliente.estado = 'TX'
        cliente.codigo_postal = '78701'
        cliente.pais = 'USA'

        cotizacion = MagicMock()
        cotizacion.id = 42
        cotizacion.total = Decimal('10.00')
        cotizacion.vendedor_id = None
        cotizacion.nota_cliente = ''
        cotizacion.nota_backoffice = ''
        cotizacion.fecha = None
        cotizacion.cliente = cliente
        cotizacion.get_estado_display.return_value = 'Draft'
        cotizacion.items.select_related.return_value.order_by.return_value = [item]

        response = quote_pdf_response(cotizacion)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('Quote-42.pdf', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF'))
        # Brand text is inside PDF streams; ensure Tortilla logo path is not used.
        self.assertEqual(pdf_branding.get_pdf_logo_path(), '')

    @override_settings(DEMO_MODE=True)
    def test_order_pdf_filename_uses_display_ref(self):
        from config.core import pdf_branding

        pdf_branding._apply_demo_brand_palette()

        item = MagicMock()
        item.cantidad_solicitada = 1
        item.cantidad = 1
        item.precio = Decimal('10.00')
        item.subtotal = Decimal('10.00')
        item.es_regalo = False
        item.descuento_linea_total = Decimal('0')
        item.precio_unitario_neto = Decimal('10.00')
        item.presentacion = _fake_presentacion()

        cliente = MagicMock()
        cliente.nombre_empresa = 'Nova Pantry'
        cliente.direccion = ''
        cliente.ciudad = ''
        cliente.estado = ''
        cliente.codigo_postal = ''
        cliente.pais = ''

        pedido = MagicMock()
        pedido.numero_display = '9-P1'
        pedido.total = Decimal('10.00')
        pedido.vendedor_id = None
        pedido.nota_cliente = ''
        pedido.creada_en = None
        pedido.cliente = cliente
        pedido.get_estado_display.return_value = 'Received'
        pedido.items.select_related.return_value.order_by.return_value = [item]

        response = order_pdf_response(pedido)
        self.assertIn('Order-9-P1.pdf', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF'))
