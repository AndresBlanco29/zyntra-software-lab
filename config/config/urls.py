from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.views.static import serve

from .sitemaps import sitemaps

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('quickbooks/', include('config.integrations.quickbooks.urls')),
    path('', include('config.core.urls')),
    path('', include('config.productos.urls')),
    path('cotizaciones/', include('config.cotizaciones.urls')),
    path('pedidos/', include('config.pedidos.urls')),
    path('carrito/', include('config.carrito.urls')),
    path('vendedores/', include('config.vendedores.urls')),
    path('clientes/', include('config.clientes.urls')),
    path('facturacion/', include('config.facturacion.urls')),
    path('inventario/', include('config.inventario.urls')),
    path('reportes/', include('config.reportes.urls')),
    path('auditoria/', include('config.auditoria.urls')),
    path('notificaciones/', include('config.notificaciones.urls')),
    path('ai-assistant/', include('config.ai_assistant.urls')),
    path('', include('config.usuarios.urls')),
    path('i18n/', include('django.conf.urls.i18n')),
]

if settings.DEBUG and not getattr(settings, 'USE_CLOUDINARY_MEDIA', False):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif getattr(settings, 'SERVE_MEDIA', False) and not getattr(settings, 'USE_CLOUDINARY_MEDIA', False):
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]