from django.urls import path
from .views import catalogo

urlpatterns = [
    path('catalogo/', catalogo, name='catalogo'),
]