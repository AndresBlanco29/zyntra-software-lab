from django.urls import path

from . import views

urlpatterns = [
    path('', views.audit_log_list, name='audit_log_list'),
    path('<int:log_id>/json/', views.audit_log_detail_json, name='audit_log_detail_json'),
    path('export/csv/', views.audit_log_export_csv, name='audit_log_export_csv'),
    path('export/excel/', views.audit_log_export_excel, name='audit_log_export_excel'),
    path('export/pdf/', views.audit_log_export_pdf, name='audit_log_export_pdf'),
]
