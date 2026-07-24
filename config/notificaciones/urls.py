from django.urls import path

from . import views

urlpatterns = [
    path('dispatch-alerts/mark-seen/', views.mark_dispatch_alerts_seen_view, name='mark_dispatch_alerts_seen'),
    path('dispatch-alerts/feed/', views.dispatch_alerts_feed_view, name='dispatch_alerts_feed'),
    path(
        'customer-requests/mark-seen/',
        views.mark_customer_request_alerts_seen_view,
        name='mark_customer_request_alerts_seen',
    ),
    path(
        'customer-requests/feed/',
        views.customer_request_alerts_feed_view,
        name='customer_request_alerts_feed',
    ),
]
