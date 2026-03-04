from django.shortcuts import redirect, render
from .models import Cotizacion, CotizacionItem
from clientes.models import Cliente
from productos.models import Presentacion, Producto
from decimal import Decimal


def agregar_a_cotizacion(request, presentacion_id):

    presentacion = Presentacion.objects.get(id=presentacion_id)

    carrito = request.session.get('carrito', {})

    if str(presentacion_id) in carrito:

        carrito[str(presentacion_id)]['cantidad'] += 1

    else:

        carrito[str(presentacion_id)] = {

            'nombre': presentacion.producto.nombre,
            'precio': float(presentacion.precio),
            'cantidad': 1
        }

    request.session['carrito'] = carrito

    return redirect('catalogo')

def ver_cotizacion(request):

    carrito_session = request.session.get("carrito", {})
    carrito = []
    total = 0

    for producto_id, item in carrito_session.items():

        try:
            producto = Producto.objects.get(id=producto_id)
        except Producto.DoesNotExist:
            continue

        subtotal = item["precio"] * item["cantidad"]
        total += subtotal

        carrito.append({
            "id": producto_id,
            "producto": producto,
            "nombre": producto.nombre,
            "presentacion_id": item.get("presentacion_id"),
            "precio": item["precio"],
            "cantidad": item["cantidad"],
            "subtotal": subtotal,
        })

    context = {
        "carrito": carrito,
        "total": total,
    }

    return render(request, "cotizaciones/ver_cotizacion.html", context)

def guardar_cotizacion(request):

    carrito = request.session.get('carrito', {})

    cliente = Cliente.objects.get(usuario=request.user)

    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        vendedor=request.user if request.user.role == 'VENDEDOR' else None,
        total=0
    )

    total = 0

    for presentacion_id, item in carrito.items():

        presentacion = Presentacion.objects.get(id=presentacion_id)

        subtotal = item['cantidad'] * presentacion.precio

        CotizacionItem.objects.create(
            cotizacion=cotizacion,
            presentacion=presentacion,
            cantidad=item['cantidad'],
            precio=presentacion.precio,
            subtotal=subtotal
        )

        total += subtotal

    cotizacion.total = total
    cotizacion.save()

    request.session['carrito'] = {}

    return redirect('catalogo')