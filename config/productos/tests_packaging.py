from django.test import TestCase

from config.productos.packaging import parse_case_packaging_from_product_name


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

    def test_returns_none_when_pattern_missing(self):
        self.assertIsNone(parse_case_packaging_from_product_name('Jarritos Mango'))
