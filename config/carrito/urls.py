from django.urls import path
from . import views

urlpatterns = [
    path('actualizar/', views.actualizar_cantidad, name='actualizar_cantidad'),
    path('cambiar-presentacion/', views.cambiar_presentacion, name='cambiar_presentacion'),
    path('eliminar/', views.eliminar_producto, name='eliminar_producto'),
    path('agregar/', views.agregar_carrito, name='agregar_carrito'),
]