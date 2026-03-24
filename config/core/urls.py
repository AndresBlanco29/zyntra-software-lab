from django.urls import path
from .views import health, home

urlpatterns = [
    path('health/', health, name='health'),
    path('', home, name="home"),
]