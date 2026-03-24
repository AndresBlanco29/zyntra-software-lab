from django.http import HttpResponse
from django.urls import path

def test(request):
    return HttpResponse("FUNCIONA")

urlpatterns = [
    path('', test),
]