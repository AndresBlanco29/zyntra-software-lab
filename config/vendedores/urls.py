from django.urls import path
from . import views

urlpatterns = [
    path('', views.vendedor_home, name='vendedor_home'),
    path('crear-cliente/', views.crear_cliente, name='crear_cliente'),
    path('clientes/', views.clientes, name='vendedores_clientes'),
    path('tomar-pedido/', views.tomar_pedido, name='tomar_pedido'),
    path('tomar-cotizacion/', views.tomar_cotizacion, name='tomar_cotizacion'),
    path('pedido/catalogo/<int:cliente_id>/', views.catalogo_vendedor, name='catalogo_vendedor'),
    path('pedido/resumen/',views.ver_pedido,name='ver_pedido'),
    path('pedido/agregar/',views.agregar_producto_pedido,name='agregar_producto_pedido'),
    path('pedido/combo/<int:promocion_id>/miembros/', views.combo_pedido_miembros, name='combo_pedido_miembros'),
    path("pedido/enviar/", views.enviar_pedido, name="enviar_pedido"),
    path("pedido/crear-cotizacion/", views.crear_cotizacion_desde_toma, name="crear_cotizacion_desde_toma"),
    path("pedido/nota/", views.guardar_nota_pedido, name="guardar_nota_pedido"),
    path("pedido/eliminar/",views.eliminar_producto_pedido,name="eliminar_producto_pedido"),
    path("pedido/actualizar/",views.actualizar_cantidad_pedido,name="actualizar_cantidad_pedido"),
    path('editar-cliente/', views.editar_cliente, name='editar_cliente'),
    path('configurar-terminos-cliente/', views.configurar_terminos_cliente, name='configurar_terminos_cliente'),
    path('configurar-limite-credito-cliente/', views.configurar_limite_credito_cliente, name='configurar_limite_credito_cliente'),
    path('desactivar-cliente/', views.desactivar_cliente, name='desactivar_cliente'),
    path('activar-cliente/', views.activar_cliente, name='activar_cliente'),
    path('configurar-acceso-cliente/', views.configurar_acceso_cliente, name='configurar_acceso_cliente'),
    path('acceso-cliente/<int:cliente_id>/', views.obtener_acceso_cliente, name='obtener_acceso_cliente'),
    path('notas/', views.vendedor_notes_list, name='vendedor_notes_list'),
    path('notas/credit-memo/', views.vendedor_credit_memo_create, name='vendedor_credit_memo_create'),
    path('notas/return/', views.vendedor_return_create, name='vendedor_return_create'),
]
