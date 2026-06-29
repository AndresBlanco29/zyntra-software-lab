from django.urls import path

from . import views


urlpatterns = [
	path('backoffice/invoices/', views.backoffice_invoices_list, name='backoffice_invoices_list'),
	path('backoffice/notes/', views.backoffice_adjustment_notes_list, name='backoffice_adjustment_notes_list'),
	path('backoffice/notes/create/', views.backoffice_adjustment_note_create, name='backoffice_adjustment_note_create'),
	path('backoffice/invoices/live-drivers/', views.backoffice_live_drivers, name='backoffice_live_drivers'),
	path('backoffice/invoices/live-drivers/data/', views.backoffice_live_drivers_data, name='backoffice_live_drivers_data'),
	path('backoffice/invoices/<int:invoice_id>/', views.backoffice_invoice_detail, name='backoffice_invoice_detail'),
	path('backoffice/invoices/<int:invoice_id>/tracking/', views.backoffice_invoice_live_tracking, name='backoffice_invoice_live_tracking'),
	path('backoffice/invoices/<int:invoice_id>/tracking/data/', views.backoffice_invoice_tracking_data, name='backoffice_invoice_tracking_data'),
	path('backoffice/invoices/<int:invoice_id>/void/', views.backoffice_invoice_void, name='backoffice_invoice_void'),
	path('backoffice/invoices/<int:invoice_id>/delete/', views.backoffice_invoice_delete, name='backoffice_invoice_delete'),
	path('backoffice/void-records/', views.backoffice_void_records_list, name='backoffice_void_records_list'),
	path('backoffice/invoices/<int:invoice_id>/pdf/', views.backoffice_invoice_pdf, name='backoffice_invoice_pdf'),
	path('backoffice/invoices/<int:invoice_id>/notes/create/', views.backoffice_invoice_create_note, name='backoffice_invoice_create_note'),
	path('backoffice/notes/<int:note_id>/approve/', views.backoffice_invoice_approve_note, name='backoffice_invoice_approve_note'),
	path('backoffice/notes/<int:note_id>/cancel/', views.backoffice_invoice_cancel_note, name='backoffice_invoice_cancel_note'),
	path('backoffice/notes/<int:note_id>/delete/', views.backoffice_invoice_delete_note, name='backoffice_invoice_delete_note'),
	path('backoffice/deliveries/<int:delivery_id>/unlock-client/', views.backoffice_unlock_delivery_client, name='backoffice_unlock_delivery_client'),
	path('backoffice/deliveries/<int:delivery_id>/mark-unpaid/', views.backoffice_mark_delivery_unpaid, name='backoffice_mark_delivery_unpaid'),
	path('backoffice/pedidos/<int:pedido_id>/generate-invoice/', views.backoffice_generate_invoice, name='backoffice_generate_invoice'),
	path('driver/deliveries/', views.driver_delivery_list, name='driver_delivery_list'),
	path('driver/deliveries/route/', views.driver_delivery_route, name='driver_delivery_route'),
	path('driver/deliveries/<int:delivery_id>/', views.driver_delivery_detail, name='driver_delivery_detail'),
	path('driver/deliveries/<int:delivery_id>/evidence/', views.driver_delivery_upload_evidence, name='driver_delivery_upload_evidence'),
	path('driver/deliveries/<int:delivery_id>/tracking/', views.driver_delivery_tracking, name='driver_delivery_tracking'),
	path('driver/deliveries/<int:delivery_id>/tracking/update/', views.driver_delivery_update_location, name='driver_delivery_update_location'),
	path('driver/deliveries/<int:delivery_id>/start-route/', views.driver_delivery_start_route, name='driver_delivery_start_route'),
	path('driver/deliveries/<int:delivery_id>/complete/', views.driver_delivery_complete, name='driver_delivery_complete'),
	path('driver/deliveries/<int:delivery_id>/notes/create/', views.driver_delivery_create_note, name='driver_delivery_create_note'),
	path('driver/deliveries/<int:delivery_id>/invoice-pdf/', views.driver_invoice_pdf, name='driver_invoice_pdf'),
]