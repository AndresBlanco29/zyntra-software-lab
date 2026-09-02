"""Demo environment guards.

Production stays untouched when DEMO_MODE is False (default).
These helpers must never enable production QuickBooks while DEMO_MODE is on.
"""

from __future__ import annotations

ALLOWED_DEMO_QUICKBOOKS_PROVIDERS = frozenset({'mock', 'sandbox'})
FORBIDDEN_DEMO_QUICKBOOKS_ENVIRONMENTS = frozenset({'production', 'prod'})


class DemoConfigurationError(RuntimeError):
    """Raised when DEMO_MODE is misconfigured in a way that could touch production."""


def normalize_quickbooks_provider(value: str | None, *, demo_mode: bool) -> str:
    raw = (value or '').strip().lower()
    if demo_mode:
        if not raw:
            return 'mock'
        if raw not in ALLOWED_DEMO_QUICKBOOKS_PROVIDERS:
            raise DemoConfigurationError(
                'DEMO_MODE requires QUICKBOOKS_PROVIDER to be "mock" or "sandbox". '
                f'Got {value!r}.'
            )
        return raw
    if not raw:
        return 'live'
    if raw == 'live':
        return 'live'
    if raw in ALLOWED_DEMO_QUICKBOOKS_PROVIDERS:
        # Non-demo may still use sandbox via QUICKBOOKS_ENVIRONMENT; provider "live"
        # means the real Intuit client path.
        return 'live'
    return 'live'


def validate_demo_quickbooks_isolation(
    *,
    demo_mode: bool,
    quickbooks_environment: str,
    quickbooks_provider: str,
) -> None:
    """Fail boot when DEMO could talk to QuickBooks production."""
    if not demo_mode:
        return

    environment = (quickbooks_environment or '').strip().lower()
    provider = (quickbooks_provider or '').strip().lower()

    if environment in FORBIDDEN_DEMO_QUICKBOOKS_ENVIRONMENTS:
        raise DemoConfigurationError(
            'DEMO_MODE=1 forbids QUICKBOOKS_ENVIRONMENT=production. '
            'Use QUICKBOOKS_PROVIDER=mock (Showcase) or sandbox credentials '
            'that do not belong to La Tortilla Grocery production.'
        )

    if provider not in ALLOWED_DEMO_QUICKBOOKS_PROVIDERS:
        raise DemoConfigurationError(
            'DEMO_MODE=1 forbids production QuickBooks provider. '
            'Set QUICKBOOKS_PROVIDER=mock or QUICKBOOKS_PROVIDER=sandbox.'
        )

    if provider == 'sandbox' and environment in FORBIDDEN_DEMO_QUICKBOOKS_ENVIRONMENTS:
        raise DemoConfigurationError(
            'DEMO_MODE sandbox provider cannot use QUICKBOOKS_ENVIRONMENT=production.'
        )


def build_demo_isolation_report(settings_module) -> dict:
    """Structured report for `check_demo_isolation` management command."""
    demo_mode = bool(getattr(settings_module, 'DEMO_MODE', False))
    provider = getattr(settings_module, 'QUICKBOOKS_PROVIDER', 'live')
    environment = getattr(settings_module, 'QUICKBOOKS_ENVIRONMENT', '')
    db = settings_module.DATABASES.get('default', {})
    checks = [
        {
            'id': 'demo_mode',
            'ok': demo_mode,
            'detail': 'DEMO_MODE is enabled' if demo_mode else 'DEMO_MODE is off (production posture)',
        },
        {
            'id': 'quickbooks_provider',
            'ok': (not demo_mode) or provider in ALLOWED_DEMO_QUICKBOOKS_PROVIDERS,
            'detail': f'QUICKBOOKS_PROVIDER={provider}',
        },
        {
            'id': 'quickbooks_environment',
            'ok': (not demo_mode) or environment not in FORBIDDEN_DEMO_QUICKBOOKS_ENVIRONMENTS,
            'detail': f'QUICKBOOKS_ENVIRONMENT={environment}',
        },
        {
            'id': 'outbound_email',
            'ok': (not demo_mode) or bool(getattr(settings_module, 'DEMO_DISABLE_OUTBOUND_EMAIL', False)),
            'detail': (
                'Outbound email disabled for DEMO'
                if getattr(settings_module, 'DEMO_DISABLE_OUTBOUND_EMAIL', False)
                else 'Outbound email may be active'
            ),
        },
        {
            'id': 'cloudinary',
            'ok': (not demo_mode) or not bool(getattr(settings_module, 'USE_CLOUDINARY_MEDIA', False)),
            'detail': f'USE_CLOUDINARY_MEDIA={getattr(settings_module, "USE_CLOUDINARY_MEDIA", False)}',
        },
        {
            'id': 'ai_assistant',
            # Demo may enable Zyntra Guide only with mock provider (no OpenAI).
            'ok': (not demo_mode)
            or (not bool(getattr(settings_module, 'AI_ASSISTANT_ENABLED', False)))
            or (
                str(getattr(settings_module, 'AI_ASSISTANT_PROVIDER', 'live') or 'live').lower()
                == 'mock'
            ),
            'detail': (
                f'AI_ASSISTANT_ENABLED={getattr(settings_module, "AI_ASSISTANT_ENABLED", False)} '
                f'PROVIDER={getattr(settings_module, "AI_ASSISTANT_PROVIDER", "live")}'
            ),
        },
        {
            'id': 'cors',
            'ok': (not demo_mode) or not bool(getattr(settings_module, 'CORS_ALLOW_ALL_ORIGINS', True)),
            'detail': f'CORS_ALLOW_ALL_ORIGINS={getattr(settings_module, "CORS_ALLOW_ALL_ORIGINS", True)}',
        },
        {
            'id': 'database_engine',
            'ok': True,
            'detail': f"ENGINE={db.get('ENGINE')} NAME={db.get('NAME')}",
        },
    ]
    return {
        'demo_mode': demo_mode,
        'passed': all(item['ok'] for item in checks if demo_mode or item['id'] == 'demo_mode'),
        'checks': checks,
    }
