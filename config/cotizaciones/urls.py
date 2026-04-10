from django.urls import path

from .views import (
    abrir_whatsapp_manual_cotizacion,
    agregar_a_cotizacion,
    backoffice_cotizacion_detalle,
    backoffice_cotizaciones,
    cliente_cotizacion_recibida_detalle,
    cliente_cotizaciones_recibidas,
    eliminar_producto,
    enviar_cotizacion_cliente,
    guardar_cotizacion,
    ver_cotizacion,
)


urlpatterns = [
    path('agregar/', agregar_a_cotizacion, name='agregar_a_cotizacion'),
    path('ver/', ver_cotizacion, name='ver_cotizacion'),
    path('guardar/', guardar_cotizacion, name='guardar_cotizacion'),
    path('eliminar/', eliminar_producto, name='eliminar_producto'),
    path('backoffice/', backoffice_cotizaciones, name='backoffice_cotizaciones'),
    path('backoffice/<int:cotizacion_id>/', backoffice_cotizacion_detalle, name='backoffice_cotizacion_detalle'),
    path('backoffice/<int:cotizacion_id>/enviar/', enviar_cotizacion_cliente, name='enviar_cotizacion_cliente'),
    path('backoffice/<int:cotizacion_id>/whatsapp/', abrir_whatsapp_manual_cotizacion, name='abrir_whatsapp_manual_cotizacion'),
    path('cliente/recibidas/', cliente_cotizaciones_recibidas, name='cliente_cotizaciones_recibidas'),
    path('cliente/recibidas/<uuid:token>/', cliente_cotizacion_recibida_detalle, name='cliente_cotizacion_recibida_detalle'),
]
