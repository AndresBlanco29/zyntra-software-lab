from django.test import SimpleTestCase, override_settings
from reportlab.lib import colors

from config.core.demo_branding import (
    DEMO_ADDRESS_LINE_1,
    DEMO_EMAIL,
    DEMO_PHONE_DISPLAY,
)


class PdfBrandingDemoTests(SimpleTestCase):
    @override_settings(DEMO_MODE=True, DEMO_BRAND_LEGAL_NAME='Zyntra')
    def test_demo_pdf_has_no_tortilla_logo_or_contact(self):
        from config.core import pdf_branding

        pdf_branding._apply_demo_brand_palette()

        self.assertEqual(pdf_branding.BRAND_NAME, 'Zyntra')
        self.assertEqual(pdf_branding.get_pdf_logo_path(), '')
        self.assertIsNone(pdf_branding.build_pdf_logo_image(max_width=36, max_height=36))

        lines = pdf_branding.get_pdf_company_contact_lines()
        blob = ' '.join(lines).lower()
        self.assertIn(DEMO_ADDRESS_LINE_1.lower(), blob)
        self.assertIn(DEMO_EMAIL.lower(), blob)
        self.assertIn(DEMO_PHONE_DISPLAY.lower(), blob)
        self.assertNotIn('tortilla', blob)
        self.assertNotIn('marietta', blob)
        self.assertNotIn('latortilla', blob)
        self.assertNotIn('470', blob)

    @override_settings(DEMO_MODE=True)
    def test_demo_pdf_palette_is_zyntra_aurora_not_tortilla_blue(self):
        from config.core import pdf_branding

        pdf_branding._apply_demo_brand_palette()
        self.assertEqual(pdf_branding.BRAND_PRIMARY, colors.HexColor('#0B1224'))
        self.assertEqual(pdf_branding.BRAND_ACCENT_HEX.upper(), '#A78BFA')
        self.assertNotEqual(pdf_branding.BRAND_ACCENT_HEX.upper(), '#FFD400')
