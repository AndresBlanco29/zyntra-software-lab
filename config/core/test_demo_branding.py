from django.test import SimpleTestCase, override_settings

from config.core.demo_branding import (
    DEMO_EMAIL,
    DEMO_PHONE_DISPLAY,
    DEMO_WHATSAPP_E164,
    apply_demo_home_contenido,
    get_active_brand_legal_name,
    get_active_brand_name,
    get_demo_brand_name,
    get_demo_contact_context,
    get_demo_home_contenido_defaults,
)


class DemoBrandingTests(SimpleTestCase):
    @override_settings(DEMO_MODE=False)
    def test_production_keeps_ltg_brand(self):
        self.assertEqual(get_active_brand_name(), 'La Tortilla Grocery')
        self.assertEqual(get_active_brand_legal_name(), 'La Tortilla Grocery LLC')

    @override_settings(DEMO_MODE=True, DEMO_BRAND_NAME='Zyntra', DEMO_BRAND_LEGAL_NAME='Zyntra')
    def test_demo_uses_zyntra(self):
        self.assertEqual(get_demo_brand_name(), 'Zyntra')
        self.assertEqual(get_active_brand_name(), 'Zyntra')
        self.assertEqual(get_active_brand_legal_name(), 'Zyntra')

    @override_settings(DEMO_MODE=True)
    def test_demo_contact_is_fictitious(self):
        contact = get_demo_contact_context()
        self.assertEqual(contact['DEMO_PHONE_DISPLAY'], DEMO_PHONE_DISPLAY)
        self.assertEqual(contact['DEMO_EMAIL'], DEMO_EMAIL)
        self.assertIn(DEMO_WHATSAPP_E164, contact['DEMO_WHATSAPP_URL'])
        self.assertNotIn('470', contact['DEMO_PHONE_DISPLAY'])
        self.assertNotIn('latortilla', contact['DEMO_EMAIL'])

    @override_settings(DEMO_MODE=True, DEMO_BRAND_NAME='Zyntra')
    def test_demo_home_defaults_drop_ltg_copy(self):
        defaults = get_demo_home_contenido_defaults()
        blob = ' '.join(str(v) for v in defaults.values()).lower()
        self.assertIn('zyntra', blob)
        self.assertNotIn('tortilla', blob)
        self.assertNotIn('marietta', blob)
        self.assertNotIn('latortilla', blob)

    @override_settings(DEMO_MODE=True, DEMO_BRAND_NAME='Zyntra')
    def test_apply_demo_home_contenido_sets_fields(self):
        class Obj:
            pass

        obj = Obj()
        apply_demo_home_contenido(obj)
        self.assertEqual(obj.footer_empresa_titulo, 'Zyntra')
        self.assertEqual(obj.footer_contacto_email, DEMO_EMAIL)
