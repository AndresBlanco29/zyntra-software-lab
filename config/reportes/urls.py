from django.urls import path

from . import report_pages, views

urlpatterns = [
	path('', report_pages.business_overview, name='reportes_dashboard'),
	path('bi/', views.dashboard, name='reportes_bi'),
	path('inventory/', report_pages.inventory_report, name='reportes_inventory'),
	path('stagnant/', report_pages.stagnant_report, name='reportes_stagnant'),
	path('sales/', report_pages.sales_report, name='reportes_sales'),
	path('receivables/', report_pages.receivables_report, name='reportes_receivables'),
	path('finance/', report_pages.finance_report, name='reportes_finance'),
	path('purchases/', report_pages.purchases_report, name='reportes_purchases'),
	path('valued/', report_pages.valued_report, name='reportes_valued'),
	path('movements/', report_pages.movements_report, name='reportes_movements'),
	path('export/excel/', views.export_excel, name='reportes_export_excel'),
	path('export/pdf/', views.export_pdf, name='reportes_export_pdf'),
	path('export/csv/', views.export_csv, name='reportes_export_csv'),
	path('send-email/', views.send_email_now, name='reportes_send_email_now'),
]
