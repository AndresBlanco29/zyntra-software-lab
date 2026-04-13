from django.urls import path

from . import views


urlpatterns = [
	path('backoffice/invoices/', views.backoffice_invoices_list, name='backoffice_invoices_list'),
	path('backoffice/invoices/generate/<int:pedido_id>/', views.backoffice_generate_invoice, name='backoffice_generate_invoice'),
	path('backoffice/invoices/<int:invoice_id>/', views.backoffice_invoice_detail, name='backoffice_invoice_detail'),
	path('backoffice/invoices/<int:invoice_id>/pdf/', views.backoffice_invoice_pdf, name='backoffice_invoice_pdf'),
	path('backoffice/deliveries/<int:delivery_id>/unlock-client/', views.backoffice_unlock_delivery_client, name='backoffice_unlock_delivery_client'),
	path('backoffice/invoices/<int:invoice_id>/notes/create/', views.backoffice_invoice_create_note, name='backoffice_invoice_create_note'),
	path('backoffice/invoice-notes/<int:note_id>/approve/', views.backoffice_invoice_approve_note, name='backoffice_invoice_approve_note'),
	path('backoffice/invoice-notes/<int:note_id>/cancel/', views.backoffice_invoice_cancel_note, name='backoffice_invoice_cancel_note'),
	path('driver/deliveries/', views.driver_delivery_list, name='driver_delivery_list'),
	path('driver/deliveries/route/', views.driver_delivery_route, name='driver_delivery_route'),
	path('driver/deliveries/<int:delivery_id>/', views.driver_delivery_detail, name='driver_delivery_detail'),
	path('driver/deliveries/<int:delivery_id>/start-route/', views.driver_delivery_start_route, name='driver_delivery_start_route'),
	path('driver/deliveries/<int:delivery_id>/complete/', views.driver_delivery_complete, name='driver_delivery_complete'),
	path('driver/deliveries/<int:delivery_id>/invoice-pdf/', views.driver_invoice_pdf, name='driver_invoice_pdf'),
]