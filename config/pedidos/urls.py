from django.urls import path

from . import views

urlpatterns = [
    path('backoffice/', views.backoffice_dashboard, name='backoffice_dashboard'),
    path('backoffice/presentaciones/buscar/', views.backoffice_buscar_presentaciones, name='backoffice_buscar_presentaciones'),
    path('backoffice/ordenes/', views.backoffice_pedidos, name='backoffice_pedidos'),
    path('backoffice/pipeline/', views.backoffice_crm_pipeline, name='backoffice_crm_pipeline'),
    path('backoffice/<int:pedido_id>/', views.backoffice_pedido_detalle, name='backoffice_pedido_detalle'),
    path('backoffice/<int:pedido_id>/partial/', views.backoffice_pedido_partial, name='backoffice_pedido_partial'),
    path('backoffice/<int:pedido_id>/partial/confirm/', views.backoffice_pedido_partial_confirm, name='backoffice_pedido_partial_confirm'),
    path('backoffice/<int:pedido_id>/enviar-cliente/', views.backoffice_enviar_pedido_cliente, name='backoffice_enviar_pedido_cliente'),
    path('backoffice/<int:pedido_id>/void/', views.backoffice_pedido_void, name='backoffice_pedido_void'),
    path('backoffice/<int:pedido_id>/delete/', views.backoffice_pedido_delete, name='backoffice_pedido_delete'),
    path('backoffice/<int:pedido_id>/edit-lock/ping/', views.backoffice_pedido_edit_lock_ping, name='backoffice_pedido_edit_lock_ping'),
    path('backoffice/<int:pedido_id>/edit-lock/release/', views.backoffice_pedido_edit_lock_release, name='backoffice_pedido_edit_lock_release'),
    path('backoffice/<int:pedido_id>/asignar-picking/', views.backoffice_asignar_picking, name='backoffice_asignar_picking'),
    path('backoffice/<int:pedido_id>/desbloquear-picking/', views.backoffice_resolver_bloqueo_picking, name='backoffice_resolver_bloqueo_picking'),
    path('backoffice/<int:pedido_id>/resolver-comentario/', views.backoffice_resolver_nota_cliente, name='backoffice_resolver_nota_cliente'),
    path('backoffice/<int:pedido_id>/credit-limit/resolve/', views.backoffice_resolve_credit_limit, name='backoffice_resolve_credit_limit'),
    path('backoffice/<int:pedido_id>/picking-ticket/', views.backoffice_picking_ticket, name='backoffice_picking_ticket'),
    path('backoffice/<int:pedido_id>/picking-ticket/pdf/', views.backoffice_picking_pdf, name='backoffice_picking_pdf'),
    path('backoffice/<int:pedido_id>/inventory-needs/pdf/', views.backoffice_inventory_needs_pdf, name='backoffice_inventory_needs_pdf'),
    path('seleccionador/picking/', views.selector_picking_list, name='selector_picking_list'),
    path('seleccionador/picking/<int:pedido_id>/', views.selector_picking_detail, name='selector_picking_detail'),
]
