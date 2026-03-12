from django.shortcuts import render
from productos.models import Producto, Marca
from .models import Testimonio


def chunk(lista, n):
    """Divide la lista en grupos de n"""
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def home(request):

    productos_destacados = list(
        Producto.objects.filter(
            activo=True,
            destacado=True
        ).prefetch_related("presentaciones")
    )

    # dividir en grupos de 3
    ofertas_chunks = list(chunk(productos_destacados, 3))

    marcas = Marca.objects.all()

    # NUEVO: traer testimonios activos
    testimonios = Testimonio.objects.filter(
        activo=True
    )

    return render(request, "home.html", {
        "ofertas_chunks": ofertas_chunks,
        "marcas": marcas,
        "testimonios": testimonios   # 👈 enviar al template
    })