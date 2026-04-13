from django.urls import path

from . import views


urlpatterns = [
	path('backoffice/', views.backoffice_inventory_list, name='backoffice_inventory_list'),
	path('backoffice/<int:presentacion_id>/', views.backoffice_inventory_detail, name='backoffice_inventory_detail'),
]