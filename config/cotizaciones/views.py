from django.shortcuts import redirect, render
from .models import Cotizacion, CotizacionItem
from config.clientes.models import Cliente
from config.productos.models import Presentacion, Producto
from decimal import Decimal
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone
import pytz


def agregar_a_cotizacion(request):

    if request.method == "POST":

        presentacion_id = request.POST.get("presentacion_id")
        cantidad = int(request.POST.get("cantidad"))

        presentacion = Presentacion.objects.get(id=presentacion_id)
        producto = presentacion.producto

        carrito = request.session.get("carrito", {})

        key = str(presentacion_id)

        if key in carrito:
            carrito[key]["cantidad"] += cantidad
        else:
            carrito[key] = {
                "producto_id": producto.id,
                "presentacion_id": presentacion.id,
                "nombre": producto.nombre,
                "cantidad": cantidad
            }

        request.session["carrito"] = carrito

        total_items = sum(item["cantidad"] for item in carrito.values())

        return JsonResponse({
            "success": True,
            "total_items": total_items
        })

def ver_cotizacion(request):

    carrito_session = request.session.get("carrito", {})
    carrito = []

    for presentacion_id, item in carrito_session.items():

        try:
            presentacion = Presentacion.objects.get(id=presentacion_id)
        except Presentacion.DoesNotExist:
            continue

        producto = presentacion.producto

        carrito.append({
            "id": presentacion_id,
            "producto": producto,
            "presentacion": presentacion,
            "cantidad": item["cantidad"],
        })

    return render(request, "cotizaciones/ver_cotizacion.html", {
        "carrito": carrito
    })

def eliminar_producto(request):

    if request.method == "POST":

        producto_id = request.POST.get("producto_id")

        carrito = request.session.get("carrito", {})

        if producto_id in carrito:
            del carrito[producto_id]

        request.session["carrito"] = carrito

        total_items = sum(item["cantidad"] for item in carrito.values())

        return JsonResponse({
            "success": True,
            "total_items": total_items
        })

def guardar_cotizacion(request):

    carrito = request.session.get('carrito', {})

    nota = request.POST.get("nota", "")

    cliente = Cliente.objects.get(usuario=request.user)

    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        vendedor=request.user,
        total=0
    )

    total = 0

    for producto_id, item in carrito.items():

        presentacion = Presentacion.objects.get(id=item["presentacion_id"])

        cantidad = item["cantidad"]

        precio = float(item.get("precio", 0))

        subtotal = precio * cantidad

        CotizacionItem.objects.create(
            cotizacion=cotizacion,
            presentacion=presentacion,
            cantidad=cantidad,
            precio=precio,
            subtotal=subtotal
        )

        total += subtotal

    cotizacion.total = total
    cotizacion.save()

    items = cotizacion.items.all()

    html_content = render_to_string(
        "emails/cotizacion_cliente.html",
        {
            "cliente": cliente,
            "items": items,
            "nota": nota
        }
    )

    email = EmailMultiAlternatives(
        subject=f"Nueva solicitud de cotización #{cotizacion.id}",
        body="Se ha recibido una nueva solicitud de cotización.",
        from_email=None,
        to=["andresilloblanco29@gmail.com"]
    )

    email.attach_alternative(html_content, "text/html")
    email.send()

    request.session['carrito'] = {}

    return redirect('catalogo')

    

