from django.shortcuts import render

def crear_cliente(request):
    return render(request, 'vendedores/crear_cliente.html')

def clientes(request):
    return render(request, 'vendedores/clientes.html')

def tomar_pedido(request):
    return render(request, 'vendedores/tomar_pedido.html')

def tomar_pedido_catalogo(request):
    return render(request, 'vendedores/tomar_pedido_catalogo.html')

def tomar_pedido_resumen(request):
    return render(request, 'vendedores/tomar_pedido_resumen.html')