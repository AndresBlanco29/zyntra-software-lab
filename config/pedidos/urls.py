from django.urls import path

from . import views

urlpatterns = [
    path('backoffice/', views.backoffice_dashboard, name='backoffice_dashboard'),
    path('backoffice/ordenes/', views.backoffice_pedidos, name='backoffice_pedidos'),
    path('backoffice/<int:pedido_id>/', views.backoffice_pedido_detalle, name='backoffice_pedido_detalle'),
    path('backoffice/<int:pedido_id>/asignar-picking/', views.backoffice_asignar_picking, name='backoffice_asignar_picking'),
    path('backoffice/<int:pedido_id>/picking-ticket/', views.backoffice_picking_ticket, name='backoffice_picking_ticket'),
    path('backoffice/<int:pedido_id>/picking-ticket/pdf/', views.backoffice_picking_pdf, name='backoffice_picking_pdf'),
    path('seleccionador/picking/', views.selector_picking_list, name='selector_picking_list'),
    path('seleccionador/picking/<int:pedido_id>/', views.selector_picking_detail, name='selector_picking_detail'),
]
