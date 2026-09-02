from django.urls import path
from .demo_views import reset_demo
from .views import health, home, privacy_policy, terms_of_service

urlpatterns = [
    path('health/', health, name='health'),
    path('', home, name="home"),
    path('privacy/', privacy_policy, name='privacy'),
    path('terms/', terms_of_service, name='terms'),
    path('demo/reset/', reset_demo, name='demo_reset'),
]