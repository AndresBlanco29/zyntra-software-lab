from django.urls import path

from . import views

urlpatterns = [
    path('dispatch-alerts/mark-seen/', views.mark_dispatch_alerts_seen_view, name='mark_dispatch_alerts_seen'),
]
