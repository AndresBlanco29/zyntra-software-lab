"""Allow the marketing site to iframe a limited DEMO surface."""

from __future__ import annotations

from django.contrib.auth import get_user_model, login
from django.http import HttpResponseRedirect
from django.utils.deprecation import MiddlewareMixin

from config.core.demo_embed import (
    default_embed_redirect,
    demo_embed_enabled,
    embed_login_email,
    embed_path_allowed,
    frame_ancestors_header,
    mark_embed_session,
    request_wants_embed,
)


class DemoEmbedMiddleware(MiddlewareMixin):
    """
    When DEMO_MODE + embed=1:
    - auto-login showcase backoffice user (no production data)
    - block navigation outside Pedidos / Catálogo / Reportes
    - allow framing from the marketing site
    """

    def process_request(self, request):
        if not demo_embed_enabled():
            request.demo_embed = False
            return None

        wants_embed = request_wants_embed(request)
        request.demo_embed = wants_embed
        if not wants_embed:
            return None

        mark_embed_session(request)

        if not request.user.is_authenticated:
            User = get_user_model()
            user = User.objects.filter(email__iexact=embed_login_email()).first()
            if user is None:
                user = User.objects.filter(username='demo_backoffice').first()
            if user is not None:
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')

        path = request.path
        if path.startswith('/static/') or path.startswith('/media/'):
            return None

        if not embed_path_allowed(path):
            return HttpResponseRedirect(default_embed_redirect())

        return None

    def process_response(self, request, response):
        if not demo_embed_enabled():
            return response

        if getattr(request, 'demo_embed', False) or request.GET.get('embed') == '1':
            # Cross-origin iframe on desirelogic.com / localhost Vite.
            if 'X-Frame-Options' in response:
                del response['X-Frame-Options']
            response['Content-Security-Policy'] = frame_ancestors_header()
        return response
