from django.shortcuts import render
from .models import Producto

def catalogo(request):

    productos = Producto.objects.all()

    context = {
        'productos': productos
    }

    return render(request, 'productos/catalogo.html', context)
