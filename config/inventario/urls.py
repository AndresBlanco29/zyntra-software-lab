from django.urls import path

from . import views

urlpatterns = [
    path('backoffice/', views.backoffice_inventory_list, name='backoffice_inventory_list'),
    path('backoffice/<int:presentacion_id>/', views.backoffice_inventory_detail, name='backoffice_inventory_detail'),
    path('backoffice/suppliers/', views.backoffice_supplier_list, name='backoffice_supplier_list'),
    path('backoffice/suppliers/<int:supplier_id>/', views.backoffice_supplier_detail, name='backoffice_supplier_detail'),
    path('backoffice/purchase-orders/', views.backoffice_supplier_purchase_list, name='backoffice_supplier_purchase_list'),
    path('backoffice/purchase-orders/<int:compra_id>/', views.backoffice_supplier_purchase_detail, name='backoffice_supplier_purchase_detail'),
    path('backoffice/purchase-orders/<int:compra_id>/receive/', views.backoffice_supplier_purchase_receive, name='backoffice_supplier_purchase_receive'),
    path('backoffice/purchase-orders/<int:compra_id>/cancel/', views.backoffice_supplier_purchase_cancel, name='backoffice_supplier_purchase_cancel'),
]
