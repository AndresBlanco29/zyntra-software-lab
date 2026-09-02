from django.test import SimpleTestCase

from config.config.demo import (
    DemoConfigurationError,
    build_demo_isolation_report,
    normalize_quickbooks_provider,
    validate_demo_quickbooks_isolation,
)


class DemoQuickBooksGuardTests(SimpleTestCase):
    def test_production_provider_defaults_to_live_when_demo_off(self):
        self.assertEqual(normalize_quickbooks_provider(None, demo_mode=False), 'live')

    def test_demo_defaults_to_mock_provider(self):
        self.assertEqual(normalize_quickbooks_provider(None, demo_mode=True), 'mock')

    def test_demo_rejects_unknown_provider(self):
        with self.assertRaises(DemoConfigurationError):
            normalize_quickbooks_provider('production', demo_mode=True)

    def test_demo_forbids_quickbooks_production_environment(self):
        with self.assertRaises(DemoConfigurationError):
            validate_demo_quickbooks_isolation(
                demo_mode=True,
                quickbooks_environment='production',
                quickbooks_provider='mock',
            )

    def test_demo_allows_mock_sandbox_pair(self):
        validate_demo_quickbooks_isolation(
            demo_mode=True,
            quickbooks_environment='sandbox',
            quickbooks_provider='mock',
        )

    def test_non_demo_allows_production_environment(self):
        validate_demo_quickbooks_isolation(
            demo_mode=False,
            quickbooks_environment='production',
            quickbooks_provider='live',
        )


class DemoIsolationReportTests(SimpleTestCase):
    def test_report_flags_demo_mode_off(self):
        settings_stub = type(
            'S',
            (),
            {
                'DEMO_MODE': False,
                'QUICKBOOKS_PROVIDER': 'live',
                'QUICKBOOKS_ENVIRONMENT': 'production',
                'DEMO_DISABLE_OUTBOUND_EMAIL': False,
                'USE_CLOUDINARY_MEDIA': True,
                'AI_ASSISTANT_ENABLED': True,
                'CORS_ALLOW_ALL_ORIGINS': True,
                'DATABASES': {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': 'x'}},
            },
        )()
        report = build_demo_isolation_report(settings_stub)
        self.assertFalse(report['demo_mode'])
        self.assertFalse(next(c for c in report['checks'] if c['id'] == 'demo_mode')['ok'])
