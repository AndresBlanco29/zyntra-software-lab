from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='reportes_dashboard'),
    path('export/excel/', views.export_excel, name='reportes_export_excel'),
    path('export/pdf/', views.export_pdf, name='reportes_export_pdf'),
    path('export/csv/', views.export_csv, name='reportes_export_csv'),
    path('send-email/', views.send_email_now, name='reportes_send_email_now'),
]
