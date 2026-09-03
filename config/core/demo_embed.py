"""DEMO Software Lab embed helpers (iframe on the marketing site)."""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings

# Only these areas are reachable while ?embed=1 (anti-copy surface).
EMBED_ALLOWED_PREFIXES = (
    '/pedidos/backoffice/ordenes',
    '/pedidos/backoffice/',  # order detail / partials under backoffice
    '/productos/catalogo',
    '/reportes/',
    '/static/',
    '/media/',
    '/i18n/',
    '/login/',
    '/logout/',
)

EMBED_HOME_PATH = '/pedidos/backoffice/ordenes/'


def demo_embed_enabled() -> bool:
    return bool(getattr(settings, 'DEMO_MODE', False))


def request_wants_embed(request) -> bool:
    if not demo_embed_enabled():
        return False
    if request.GET.get('embed') == '1':
        return True
    if getattr(request, 'session', None) is not None:
        return bool(request.session.get('demo_embed'))
    return False


def mark_embed_session(request) -> None:
    if getattr(request, 'session', None) is not None:
        request.session['demo_embed'] = True


def embed_path_allowed(path: str) -> bool:
    path = path or '/'
    if path in {'/', '/login/', '/logout/'}:
        return True
    return any(path.startswith(prefix) for prefix in EMBED_ALLOWED_PREFIXES)


def embed_login_email() -> str:
    return (getattr(settings, 'DEMO_EMBED_USER_EMAIL', None) or 'demo@demo-system.com').strip()


def with_embed_query(path: str) -> str:
    sep = '&' if '?' in path else '?'
    return f'{path}{sep}embed=1'


def default_embed_redirect() -> str:
    return with_embed_query(EMBED_HOME_PATH)


def frame_ancestors_header() -> str:
    raw = (getattr(settings, 'DEMO_EMBED_FRAME_ANCESTORS', '') or '').strip()
    if raw:
        ancestors = ' '.join(part.strip() for part in raw.split(',') if part.strip())
    else:
        ancestors = (
            "'self' "
            'https://desirelogic.com https://www.desirelogic.com '
            'http://localhost:5173 http://127.0.0.1:5173 '
            'http://localhost:4173 http://127.0.0.1:4173'
        )
    return f"frame-ancestors {ancestors}"
