from django.urls import path
from .views import agregar_a_cotizacion, ver_cotizacion, guardar_cotizacion


urlpatterns = [
    path(
        'agregar/<int:presentacion_id>/',
        agregar_a_cotizacion, name='agregar_a_cotizacion'
    ),

    path('ver/', ver_cotizacion, name='ver_cotizacion'),

    path('guardar/', guardar_cotizacion, name='guardar_cotizacion'),
]
