from django.shortcuts import render, redirect
from .models import Producto, Categoria, Marca


def catalogo(request):

    productos = Producto.objects.filter(activo=True).prefetch_related("presentaciones")

    categorias = Categoria.objects.all()

    marcas = Marca.objects.all()

    context = {
        'productos': productos,
        'categorias': categorias,
        'marcas': marcas
    }

    return render(request, 'productos/catalogo.html', context)


