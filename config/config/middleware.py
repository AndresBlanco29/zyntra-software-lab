from django.conf import settings
from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect
from django.utils import translation
from urllib.parse import quote

from config.usuarios.schema_repair import ensure_runtime_schema


class DefaultEnglishUnlessChosenMiddleware:
    """Use English by default; only honor an explicit language cookie or session choice."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        explicit_language = self._get_explicit_language(request)
        if explicit_language and explicit_language in dict(settings.LANGUAGES):
            translation.activate(explicit_language)
            request.LANGUAGE_CODE = explicit_language
        else:
            translation.activate(settings.LANGUAGE_CODE)
            request.LANGUAGE_CODE = settings.LANGUAGE_CODE
        return self.get_response(request)

    @staticmethod
    def _get_explicit_language(request):
        language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if language:
            return language

        session_key = getattr(settings, 'LANGUAGE_SESSION_KEY', '_language')
        if hasattr(request, 'session'):
            language = request.session.get(session_key)
            if language:
                return language

        return None


class WwwRedirectMiddleware:
    """Redirige www.CANONICAL_DOMAIN → CANONICAL_DOMAIN (301 permanente).
    Sólo actúa en producción (DEBUG=False) y cuando CANONICAL_DOMAIN está definido."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.canonical = getattr(settings, 'CANONICAL_DOMAIN', '')

    def __call__(self, request):
        host = request.get_host().split(':')[0]  # Strip puerto si lo hubiera
        if self.canonical and not settings.DEBUG and host == f'www.{self.canonical}':
            url = request.build_absolute_uri()
            url = url.replace(f'://www.{self.canonical}', f'://{self.canonical}', 1)
            return HttpResponsePermanentRedirect(url)
        return self.get_response(request)


class NoCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Static/media files should keep their normal caching behavior.
        if request.path.startswith("/static/") or request.path.startswith("/media/"):
            return response

        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


class ProtectedAreaLoginMiddleware:
    """Exige autenticación para zonas privadas por prefijo de URL."""

    # Rutas que siempre deben ser públicas.
    PUBLIC_PATH_PREFIXES = (
        "/static/",
        "/media/",
        "/quickbooks/callback/",
        "/login/",
        "/login-modal/",
        "/password-reset/",
        "/reset/",
        "/registro/",
        "/registro-modal/",
        "/verificar-username/",
        "/logout/",
        "/i18n/",
        "/health/",
    )

    PUBLIC_EXACT_PATHS = {
        "/",
        "/catalogo/",
        "/sitemap.xml",
        "/clientes/registro/",
    }

    # Zonas privadas que requieren usuario autenticado.
    # Las rutas públicas de cotización/carrito (agregar, eliminar, etc.) no entran aquí.
    PROTECTED_PATH_PREFIXES = (
        "/panel-admin/",
        "/vendedores/",
        "/productos/",
        "/pedidos/",
        "/facturacion/",
        "/reportes/",
        "/auditoria/",
        "/inventario/",
        "/notificaciones/",
        "/quickbooks/",
        "/cotizaciones/backoffice/",
        "/cotizaciones/cliente/",
        "/cotizaciones/ver/",
        "/cotizaciones/guardar/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if self._is_public_path(path):
            return self.get_response(request)

        if self._is_protected_path(path) and not request.user.is_authenticated:
            next_url = quote(request.get_full_path(), safe="")
            return HttpResponseRedirect(f"/?show_login=1&next={next_url}")

        return self.get_response(request)

    def _is_public_path(self, path):
        return path in self.PUBLIC_EXACT_PATHS or any(
            path.startswith(prefix) for prefix in self.PUBLIC_PATH_PREFIXES
        )

    def _is_protected_path(self, path):
        return any(path.startswith(prefix) for prefix in self.PROTECTED_PATH_PREFIXES)


class RuntimeSchemaRepairMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/static/') and not request.path.startswith('/media/'):
            ensure_runtime_schema()
        return self.get_response(request)
