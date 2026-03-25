from django.conf import settings
from django.http import HttpResponsePermanentRedirect


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
