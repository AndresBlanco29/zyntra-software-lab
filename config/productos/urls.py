from django.urls import path
from .views import catalogo, lista_productos, crear_producto, editar_producto, desactivar_producto, activar_producto, crear_categoria, crear_marca, lista_marcas, editar_marca, desactivar_marca, activar_marca, configurar_precios


urlpatterns = [
    path('catalogo/', catalogo, name='catalogo'),
    path('panel-admin/productos/', lista_productos, name='lista_productos'),
    path('panel-admin/marcas/', lista_marcas, name='lista_marcas'),
    path('panel-admin/productos/crear/', crear_producto, name='crear_producto'),
    path('panel-admin/productos/configurar-precios/', configurar_precios, name='configurar_precios'),
    path('panel-admin/categorias/crear/', crear_categoria, name='crear_categoria'),
    path('panel-admin/marcas/crear/', crear_marca, name='crear_marca'),
    path('panel-admin/marcas/editar/<int:marca_id>/', editar_marca, name='editar_marca'),
    path('panel-admin/marcas/desactivar/<int:marca_id>/', desactivar_marca, name='desactivar_marca'),
    path('panel-admin/marcas/activar/<int:marca_id>/', activar_marca, name='activar_marca'),
    path('productos/editar/<int:producto_id>/', editar_producto, name='editar_producto'),
    path('productos/desactivar/<int:producto_id>/', desactivar_producto, name='desactivar_producto'),
    path('productos/activar/<int:producto_id>/', activar_producto, name='activar_producto'),
    
]