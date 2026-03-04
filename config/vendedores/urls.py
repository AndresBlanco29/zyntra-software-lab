from django.urls import path
from . import views

urlpatterns = [
    path('crear-cliente/', views.crear_cliente, name='crear_cliente'),
    path('clientes/', views.clientes, name='clientes'),
    path('tomar-pedido/', views.tomar_pedido, name='tomar_pedido'),
    path('tomar-pedido/catalogo/', views.tomar_pedido_catalogo, name='tomar_pedido_catalogo'),
    path('tomar-pedido/resumen/', views.tomar_pedido_resumen, name='tomar_pedido_resumen'),

]