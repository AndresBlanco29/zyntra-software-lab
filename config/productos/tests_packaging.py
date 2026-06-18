from django.test import TestCase

from config.productos.packaging import (
    build_packaging_customer_description,
    content_type_looks_like_unit_size,
    parse_case_packaging_from_product_name,
)


class CasePackagingParserTests(TestCase):
    def test_parses_count_and_size_from_slash_pattern(self):
        parsed = parse_case_packaging_from_product_name('123 DETERGENT MAXI EFECTO COLOR 4/4.65 LT')
        self.assertEqual(parsed['units_per_case'], 4)
        self.assertEqual(parsed['unit_size_label'], '4.65 LT')
        self.assertEqual(parsed['presentation_name'], 'Caja')

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
        self.assertEqual(description, '4 unidades de tamaño 4.65 LT por caja')

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
        self.assertEqual(description, '1 paquete de 32 CT por caja')

    def test_candle_count_uses_pieces_wording(self):
        description = build_packaging_customer_description(
            units=6,
            content_type='piezas',
            presentation_name='Caja',
            language='es',
        )
        self.assertEqual(description, '6 piezas por caja')

        description_en = build_packaging_customer_description(
            units=12,
            content_type='piezas',
            presentation_name='box',
            language='en',
        )
        self.assertEqual(description_en, '12 pieces per box')
