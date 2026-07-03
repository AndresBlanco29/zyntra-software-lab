from django.test import TestCase

from config.productos.packaging import (
    build_packaging_customer_description,
    content_type_looks_like_unit_size,
    finalize_quickbooks_import_packaging,
    get_effective_packaging_for_display,
    parse_case_packaging_from_product_name,
    presentation_looks_unconfigured,
    presentation_name_looks_like_unit_size,
)


class CasePackagingParserTests(TestCase):
    def test_parses_count_and_size_from_slash_pattern(self):
        parsed = parse_case_packaging_from_product_name('123 DETERGENT MAXI EFECTO COLOR 4/4.65 LT')
        self.assertEqual(parsed['units_per_case'], 4)
        self.assertEqual(parsed['unit_size_label'], '4.65 LT')
        self.assertEqual(parsed['presentation_name'], 'caja')

    def test_parses_canola_oil_pattern(self):
        parsed = parse_case_packaging_from_product_name('ACEITE 123 CANOLA 24/ 16.91 OZ')
        self.assertEqual(parsed['units_per_case'], 24)
        self.assertEqual(parsed['unit_size_label'], '16.91 OZ')

    def test_parses_pattern_before_parenthetical_suffix(self):
        parsed = parse_case_packaging_from_product_name('ACEITE 123 CORN OLI 12/1 LT (GREEN)')
        self.assertEqual(parsed['units_per_case'], 12)
        self.assertEqual(parsed['unit_size_label'], '1 LT')

    def test_parses_count_pattern_with_ct_suffix(self):
        parsed = parse_case_packaging_from_product_name('ANAHUAC PICA LIMON 7 VERDE 24/100 CT')
        self.assertEqual(parsed['units_per_case'], 24)
        self.assertEqual(parsed['unit_size_label'], '100 CT')

    def test_parses_single_pack_count_pattern(self):
        parsed = parse_case_packaging_from_product_name('BUBA AZUL/ BLUBERRY 1/32 CT')
        self.assertEqual(parsed['units_per_case'], 1)
        self.assertEqual(parsed['unit_size_label'], '32 CT')

    def test_parses_gallon_word_suffix(self):
        parsed = parse_case_packaging_from_product_name('ACEITE MAZOLA 2/2 GALON')
        self.assertEqual(parsed['units_per_case'], 2)
        self.assertEqual(parsed['unit_size_label'], '2 GAL')

    def test_parses_simple_count_suffix_for_candles(self):
        parsed = parse_case_packaging_from_product_name('CANDLE BRILUX BLUE 14 D 6 CT')
        self.assertEqual(parsed['units_per_case'], 6)
        self.assertEqual(parsed['content_type'], 'piezas')

        parsed = parse_case_packaging_from_product_name('CANDLE BRILUX CRISTO MILAGROSO CILINDRO 12 CT')
        self.assertEqual(parsed['units_per_case'], 12)
        self.assertEqual(parsed['content_type'], 'piezas')

    def test_slash_pattern_takes_priority_over_simple_count(self):
        parsed = parse_case_packaging_from_product_name('ANAHUAC PICA LIMON 7 VERDE 24/100 CT')
        self.assertEqual(parsed['units_per_case'], 24)
        self.assertEqual(parsed['unit_size_label'], '100 CT')

    def test_parses_double_slash_pattern(self):
        parsed = parse_case_packaging_from_product_name('BOING TRIANGULO ASSORTED 3/6//6.76OZ')
        self.assertEqual(parsed['units_per_case'], 6)
        self.assertEqual(parsed['unit_size_label'], '6.76 OZ')

    def test_returns_none_when_pattern_missing(self):
        self.assertIsNone(parse_case_packaging_from_product_name('Jarritos Mango'))


class PackagingCustomerDescriptionTests(TestCase):
    def test_case_with_size_uses_units_of_size_wording(self):
        description = build_packaging_customer_description(
            units=4,
            content_type='4.65 LT',
            presentation_name='Caja',
            language='es',
        )
        self.assertEqual(description, '4 unidades de tamaño 4.65 LT por CS')

    def test_generic_content_type_keeps_simple_wording(self):
        description = build_packaging_customer_description(
            units=12,
            content_type='unidades',
            presentation_name='Pallet',
            language='es',
        )
        self.assertEqual(description, '12 unidades por pallet')

    def test_size_detector_recognizes_measurements(self):
        self.assertTrue(content_type_looks_like_unit_size('4.65 LT'))
        self.assertTrue(content_type_looks_like_unit_size('100 CT'))
        self.assertFalse(content_type_looks_like_unit_size('unidades'))

    def test_single_pack_count_uses_pack_wording(self):
        description = build_packaging_customer_description(
            units=1,
            content_type='32 CT',
            presentation_name='Caja',
            language='es',
        )
        self.assertEqual(description, '1 paquete de 32 CT por CS')

    def test_candle_count_uses_pieces_wording(self):
        description = build_packaging_customer_description(
            units=6,
            content_type='piezas',
            presentation_name='Caja',
            language='es',
        )
        self.assertEqual(description, '6 piezas por CS')

        description_en = build_packaging_customer_description(
            units=12,
            content_type='piezas',
            presentation_name='box',
            language='en',
        )
        self.assertEqual(description_en, '12 pieces per CS')


class EffectivePackagingDisplayTests(TestCase):
    def test_unconfigured_candle_uses_product_name_count_suffix(self):
        producto = type('ProductoStub', (), {'nombre': 'CANDLE BRILUX BLUE 14 D 6 CT'})()
        presentacion = type('PresentacionStub', (), {
            'unidades': 1,
            'nombre': 'Unit',
            'tipo_contenido': 'unit',
            'producto': producto,
            'producto_id': 1,
            'tipo_contenido_traducido': 'unit',
            'nombre_traducido': 'unit',
        })()

        packaging = get_effective_packaging_for_display(presentacion, language='es')

        self.assertTrue(presentation_looks_unconfigured(presentacion))
        self.assertEqual(packaging['description'], '6 piezas por CS')
        self.assertEqual(packaging['presentation_name'], 'CS')


class QuickBooksImportPackagingTests(TestCase):
    def test_finalize_uses_case_packaging_from_product_name_even_with_unit_description(self):
        finalized = finalize_quickbooks_import_packaging(
            product_name='PRUEBA 2 PRODUCTO 8/250ML',
            presentation_name='250 ML',
            tipo_contenido='unidades',
            unidades=1,
        )

        self.assertEqual(finalized['presentation_name'], 'caja')
        self.assertEqual(finalized['tipo_contenido'], '250 ML')
        self.assertEqual(finalized['unidades'], 8)

    def test_finalize_repairs_unit_size_presentation_without_slash_pattern(self):
        finalized = finalize_quickbooks_import_packaging(
            product_name='GENERIC PRODUCT',
            presentation_name='250 ML',
            tipo_contenido='unidades',
            unidades=1,
        )

        self.assertEqual(finalized['presentation_name'], 'caja')
        self.assertEqual(finalized['tipo_contenido'], '250 ML')
        self.assertEqual(finalized['unidades'], 1)

    def test_presentation_name_unit_size_detector(self):
        self.assertTrue(presentation_name_looks_like_unit_size('250 ML'))
        self.assertFalse(presentation_name_looks_like_unit_size('box'))


class MisconfiguredPresentationDisplayTests(TestCase):
    def test_effective_packaging_repairs_unit_size_presentation_name(self):
        producto = type('ProductoStub', (), {'nombre': 'PRUEBA 2 PRODUCTO 8/250ML'})()
        presentacion = type('PresentacionStub', (), {
            'unidades': 8,
            'nombre': '250 ML',
            'tipo_contenido': '250 ML',
            'producto': producto,
            'producto_id': 1,
            'tipo_contenido_traducido': '250 ML',
            'nombre_traducido': '250 ML',
        })()

        packaging = get_effective_packaging_for_display(presentacion, language='en')

        self.assertEqual(packaging['presentation_name'], 'CS')
        self.assertEqual(packaging['content_type'], '250 ML')
        self.assertEqual(packaging['units'], 8)
