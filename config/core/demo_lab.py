"""Software Lab helpers: reset protection and access checks."""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache


RESET_CONFIRMATION_PHRASE = 'RESET DEMO'
RESET_RATE_LIMIT_SECONDS = 60


def demo_lab_enabled() -> bool:
    return bool(getattr(settings, 'DEMO_MODE', False))


def user_can_reset_demo(user) -> bool:
    if not demo_lab_enabled() or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    role = (getattr(user, 'role', '') or '').strip().lower()
    return role in {'backoffice', 'admin'}


def reset_rate_limit_key(user) -> str:
    return f'demo-reset:{getattr(user, "id", "anon")}'


def reset_is_rate_limited(user) -> bool:
    return bool(cache.get(reset_rate_limit_key(user)))


def mark_reset_rate_limited(user) -> None:
    cache.set(reset_rate_limit_key(user), 1, timeout=RESET_RATE_LIMIT_SECONDS)
