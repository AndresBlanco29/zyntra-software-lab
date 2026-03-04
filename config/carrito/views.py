from django.http import JsonResponse
from django.views.decorators.http import require_POST
from productos.models import Producto, Presentacion
from django.shortcuts import render

def ver_cotizacion(request):

    carrito = request.session.get("carrito", {})
    carrito_items = []
    total = 0

    for producto_id, item in carrito.items():

        producto = Producto.objects.get(id=producto_id)

        subtotal = item["precio"] * item["cantidad"]
        total += subtotal

        carrito_items.append({
            "id": producto_id,
            "producto": producto,
            "nombre": item["nombre"],
            "presentacion_id": item["presentacion_id"],
            "precio": item["precio"],
            "cantidad": item["cantidad"],
            "subtotal": subtotal,
        })

    context = {
        "carrito": carrito_items,
        "total": total
    }

    return render(request, "carrito/mi_cotizacion.html", context)

@require_POST
def actualizar_cantidad(request):
    producto_id = request.POST.get("producto_id")
    accion = request.POST.get("accion")

    carrito = request.session.get("carrito", {})

    if producto_id in carrito:

        if accion == "sumar":
            carrito[producto_id]["cantidad"] += 1

        elif accion == "restar" and carrito[producto_id]["cantidad"] > 1:
            carrito[producto_id]["cantidad"] -= 1

        precio = float(carrito[producto_id]["precio"])
        cantidad = carrito[producto_id]["cantidad"]
        subtotal = precio * cantidad

        carrito[producto_id]["subtotal"] = subtotal
        request.session["carrito"] = carrito

        total = sum(item["precio"] * item["cantidad"] for item in carrito.values())

        return JsonResponse({
            "cantidad": cantidad,
            "subtotal": subtotal,
            "total": total
        })

    return JsonResponse({"error": True})

@require_POST
def cambiar_presentacion(request):

    producto_id = request.POST.get("producto_id")
    presentacion_id = request.POST.get("presentacion_id")

    carrito = request.session.get("carrito", {})

    if producto_id in carrito:

        presentacion = Presentacion.objects.get(id=presentacion_id)

        carrito[producto_id]["presentacion_id"] = presentacion.id
        carrito[producto_id]["precio"] = float(presentacion.precio)

        cantidad = carrito[producto_id]["cantidad"]
        subtotal = float(presentacion.precio) * cantidad

        carrito[producto_id]["subtotal"] = subtotal
        request.session["carrito"] = carrito

        total = sum(
            item["precio"] * item["cantidad"]
            for item in carrito.values()
        )

        return JsonResponse({
            "precio": presentacion.precio,
            "subtotal": subtotal,
            "total": total
        })

    return JsonResponse({"error": True})

@require_POST
def eliminar_producto(request):
    producto_id = request.POST.get("producto_id")

    carrito = request.session.get("carrito", {})

    if producto_id in carrito:
        del carrito[producto_id]
        request.session["carrito"] = carrito

    total = sum(item["precio"] * item["cantidad"] for item in carrito.values())

    return JsonResponse({
        "success": True,
        "total": total,
        "total_items": sum(item["cantidad"] for item in carrito.values())
    })

@require_POST
def agregar_carrito(request):

    producto_id = request.POST.get("producto_id")
    presentacion_id = request.POST.get("presentacion_id")
    cantidad = int(request.POST.get("cantidad"))

    producto = Producto.objects.get(id= producto_id)
    presentacion = Presentacion.objects.get(id = presentacion_id)

    carrito = request.session.get("carrito", {})

    if producto_id in carrito:

        carrito[producto_id]["cantidad"] += cantidad
    
    else:

        carrito[producto_id] = {
            "nombre": producto.nombre,
            "presentacion_id": presentacion_id,
            "presentacion_nombre": presentacion.nombre,
            "precio": float(presentacion.precio),
            "cantidad": cantidad,
        }

    # recalcular subtotal
    carrito[producto_id]["subtotal"] = (
        carrito[producto_id]["precio"] *
        carrito[producto_id]["cantidad"]
    )

    request.session["carrito"] = carrito

    total_items = sum(item["cantidad"] for item in carrito.values())

    return JsonResponse({
        "success": True,
        "total_items": total_items
    })
