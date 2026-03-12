from django.urls import path
from . import views

urlpatterns = [
    path('crear-cliente/', views.crear_cliente, name='crear_cliente'),
    path('clientes/', views.clientes, name='vendedores_clientes'),
    path('tomar-pedido/', views.tomar_pedido, name='tomar_pedido'),
    path('pedido/catalogo/<int:cliente_id>/', views.catalogo_vendedor, name='catalogo_vendedor'),
    path('pedido/resumen/',views.ver_pedido,name='ver_pedido'),
    path('pedido/agregar/',views.agregar_producto_pedido,name='agregar_producto_pedido'),
    path("pedido/enviar/", views.enviar_pedido, name="enviar_pedido"),
    path("pedido/eliminar/",views.eliminar_producto_pedido,name="eliminar_producto_pedido"),
    path("pedido/actualizar/",views.actualizar_cantidad_pedido,name="actualizar_cantidad_pedido"),

]