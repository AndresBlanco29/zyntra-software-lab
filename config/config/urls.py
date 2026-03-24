from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('config.core.urls')),
    path('', include('config.productos.urls')),
    path('cotizaciones/', include('config.cotizaciones.urls')),
    path('carrito/', include('config.carrito.urls')),
    path('vendedores/', include('config.vendedores.urls')),
    path('clientes/', include('config.clientes.urls')),
    path('', include('config.usuarios.urls')),
    path('i18n/', include('django.conf.urls.i18n')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif getattr(settings, 'SERVE_MEDIA', False):
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]