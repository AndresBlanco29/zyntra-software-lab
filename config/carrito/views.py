from decimal import Decimal

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from config.clientes.models import Cliente
from config.pedidos.services import (
    calcular_precio_unitario_neto_item,
    calcular_subtotal_item_pedido,
)
from config.productos.models import Producto, Presentacion
from config.productos.promotions import (
    _gift_session_signature,
    estado_promocion_para_linea,
    reaplicar_promociones_en_lineas_sesion,
)
from django.shortcuts import render
from django.utils.translation import gettext as _


def _line_money(item):
    if item.get("es_regalo"):
        return {
            "precio": 0.0,
            "precio_unitario_neto": 0.0,
            "subtotal": 0.0,
            "descuento_aplicado": True,
        }
    descuento_aplicado = bool(item.get("descuento_aplicado"))
    descuento_monto = item.get("descuento_monto", 0) if descuento_aplicado else 0
    precio = item.get("precio", 0)
    precio_neto = float(calcular_precio_unitario_neto_item(
        precio=precio,
        descuento_aplicado=descuento_aplicado,
        descuento_monto=descuento_monto,
    ))
    subtotal = float(calcular_subtotal_item_pedido(
        precio=precio,
        cantidad=item.get("cantidad", 0),
        descuento_aplicado=descuento_aplicado,
        descuento_monto=descuento_monto,
    ))
    return {
        "precio": float(precio or 0),
        "precio_unitario_neto": precio_neto,
        "subtotal": subtotal,
        "descuento_aplicado": descuento_aplicado,
    }


def _cart_total(carrito):
    total = Decimal("0.00")
    for item in (carrito or {}).values():
        money = _line_money(item)
        total += Decimal(str(money["subtotal"]))
    return float(total)


def _cliente_from_request(request):
    if not getattr(request.user, "is_authenticated", False):
        return None
    if getattr(request.user, "role", "") != "cliente":
        return None
    return Cliente.objects.only("nivel_precio", "estado_revision", "tipo_cliente").filter(usuario=request.user).first()


def _get_request_price_tier(request):
    if not getattr(request.user, "is_authenticated", False):
        return 1
    if getattr(request.user, "role", "") != "cliente":
        return 1

    try:
        cliente = Cliente.objects.only("nivel_precio", "estado_revision").get(usuario=request.user)
    except Cliente.DoesNotExist:
        return 1

    if cliente.estado_revision != Cliente.REVIEW_STATUS_APPROVED:
        return 1

    return cliente.get_nivel_precio_normalizado()


def _get_request_price_for_presentacion(request, presentacion):
    price_tier = _get_request_price_tier(request)
    if price_tier is None:
        return None
    return presentacion.get_price_for_tier(price_tier)


def _lineas_contexto_carrito(carrito):
    if not isinstance(carrito, dict):
        return []
    return list(carrito.values())


def _reaplicar_promociones_carrito(carrito, cliente):
    reaplicar_promociones_en_lineas_sesion(carrito, cliente=cliente)
    return carrito

def ver_cotizacion(request):

    cliente = _cliente_from_request(request)
    carrito = request.session.get("carrito", {})
    carrito_items = []
    total = 0

    _reaplicar_promociones_carrito(carrito, cliente)

    for producto_id, item in carrito.items():

        producto = Producto.objects.get(id=producto_id)
        item["producto_id"] = producto.id
        precio = item.get("precio", 0)
        subtotal = float(calcular_subtotal_item_pedido(
            precio=precio,
            cantidad=item["cantidad"],
            descuento_aplicado=item.get("descuento_aplicado", False),
            descuento_monto=item.get("descuento_monto", 0),
        ))
        total += subtotal

        carrito_items.append({
            "id": producto_id,
            "producto": producto,
            "nombre": item["nombre"],
            "presentacion_id": item["presentacion_id"],
            "precio": precio,
            "cantidad": item["cantidad"],
            "subtotal": subtotal,
            "descuento_aplicado": bool(item.get("descuento_aplicado")),
            "descuento_origen": item.get("descuento_origen") or "",
            "promocion_nombre": item.get("promocion_nombre") or "",
            "promocion_descripcion": item.get("promocion_descripcion") or "",
        })

    request.session["carrito"] = carrito

    context = {
        "carrito": carrito_items,
        "total": total
    }

    return render(request, "carrito/mi_cotizacion.html", context)

@require_POST
def actualizar_cantidad(request):

    cliente = _cliente_from_request(request)
    producto_id = request.POST.get("producto_id")
    accion = request.POST.get("accion")

    carrito = request.session.get("carrito", {})
    gifts_before = _gift_session_signature(carrito)

    if producto_id in carrito:
        if carrito[producto_id].get("es_regalo") and accion in {"sumar", "restar", "set"}:
            item = carrito[producto_id]
            return JsonResponse({
                "success": True,
                "cantidad": item.get("cantidad", 0),
                "subtotal": 0,
                "promo_applied": True,
                "promo_label": "FREE",
                "promo": {"available": False, "applied": True, "minimum": 0, "current": item.get("cantidad", 0)},
                "reload": False,
                "es_regalo": True,
            })

        if accion == "sumar":
            carrito[producto_id]["cantidad"] += 1

        elif accion == "restar":
            if carrito[producto_id]["cantidad"] > 1:
                carrito[producto_id]["cantidad"] -= 1

        elif accion == "set":
            cantidad = int(request.POST.get("cantidad", 1))
            if cantidad < 1:
                cantidad = 1
            carrito[producto_id]["cantidad"] = cantidad

        presentacion_id = carrito[producto_id].get("presentacion_id")

        if presentacion_id:
            from config.cotizaciones.views import _quote_item_price_for_customer

            presentacion = Presentacion.objects.select_related("producto").get(id=presentacion_id)
            carrito[producto_id]["producto_id"] = presentacion.producto_id
            carrito[producto_id]["precio"] = float(_quote_item_price_for_customer(
                cliente=cliente,
                presentacion=presentacion,
                session_price=carrito[producto_id].get("precio", 0),
            ))

        _reaplicar_promociones_carrito(carrito, cliente)

        cantidad = carrito[producto_id]["cantidad"]
        money = _line_money(carrito[producto_id])
        subtotal = money["subtotal"]

    else:
        money = {
            "precio": 0.0,
            "precio_unitario_neto": 0.0,
            "subtotal": 0.0,
            "descuento_aplicado": False,
        }
        subtotal = 0
        cantidad = 0

    request.session["carrito"] = carrito

    item = carrito.get(producto_id) or {}
    lineas_context = [row for row in _lineas_contexto_carrito(carrito) if not row.get("es_regalo")]
    promo_state = estado_promocion_para_linea(
        producto_id=item.get("producto_id"),
        presentacion_id=item.get("presentacion_id"),
        cantidad=item.get("cantidad"),
        precio_unitario=item.get("precio", 0),
        cliente=cliente,
        lineas_context=lineas_context,
    )
    return JsonResponse({
        "success": True,
        "cantidad": cantidad,
        "precio": money["precio"],
        "precio_unitario_neto": money["precio_unitario_neto"],
        "descuento_aplicado": money["descuento_aplicado"],
        "subtotal": subtotal,
        "total": _cart_total(carrito),
        "promo_applied": str(item.get("descuento_origen") or "") == "promocion",
        "promo_label": item.get("promocion_descripcion") or item.get("promocion_nombre") or "",
        "promo": promo_state,
        "reload": _gift_session_signature(carrito) != gifts_before,
        "es_regalo": bool(item.get("es_regalo")),
    })

@require_POST
def cambiar_presentacion(request):

    cliente = _cliente_from_request(request)
    producto_id = request.POST.get("producto_id")
    presentacion_id = request.POST.get("presentacion_id")

    carrito = request.session.get("carrito", {})

    if producto_id in carrito:

        from config.cotizaciones.views import _quote_item_price_for_customer

        presentacion = Presentacion.objects.get(id=presentacion_id)
        precio_actual = float(_quote_item_price_for_customer(
            cliente=cliente,
            presentacion=presentacion,
            session_price=carrito[producto_id].get("precio", 0),
        ))

        carrito[producto_id]["presentacion_id"] = presentacion.id
        carrito[producto_id]["producto_id"] = presentacion.producto_id
        carrito[producto_id]["precio"] = precio_actual
        _reaplicar_promociones_carrito(carrito, cliente)

        money = _line_money(carrito[producto_id])
        carrito[producto_id]["subtotal"] = money["subtotal"]
        request.session["carrito"] = carrito

        promo_state = estado_promocion_para_linea(
            producto_id=carrito[producto_id].get("producto_id"),
            presentacion_id=carrito[producto_id].get("presentacion_id"),
            cantidad=carrito[producto_id].get("cantidad"),
            precio_unitario=carrito[producto_id].get("precio", 0),
            cliente=cliente,
            lineas_context=[row for row in _lineas_contexto_carrito(carrito) if not row.get("es_regalo")],
        )
        return JsonResponse({
            "precio": money["precio"],
            "precio_unitario_neto": money["precio_unitario_neto"],
            "descuento_aplicado": money["descuento_aplicado"],
            "subtotal": money["subtotal"],
            "total": _cart_total(carrito),
            "promo_applied": str(carrito[producto_id].get("descuento_origen") or "") == "promocion",
            "promo_label": carrito[producto_id].get("promocion_descripcion") or carrito[producto_id].get("promocion_nombre") or "",
            "promo": promo_state,
        })

    return JsonResponse({"error": True})

@require_POST
def eliminar_producto(request):

    cliente = _cliente_from_request(request)
    producto_id = request.POST.get("producto_id")

    carrito = request.session.get("carrito", {})

    if producto_id in carrito:
        del carrito[producto_id]

    _reaplicar_promociones_carrito(carrito, cliente)
    request.session["carrito"] = carrito

    total_items = sum(item["cantidad"] for item in carrito.values())

    return JsonResponse({
        "success": True,
        "total_items": total_items,
        "total": _cart_total(carrito),
    })

@require_POST
def agregar_carrito(request):

    cliente = _cliente_from_request(request)
    producto_id = request.POST.get("producto_id")
    presentacion_id = request.POST.get("presentacion_id")
    cantidad = int(request.POST.get("cantidad"))

    producto = Producto.objects.get(id= producto_id)
    presentacion = Presentacion.objects.get(id = presentacion_id)
    assigned_price = _get_request_price_for_presentacion(request, presentacion)
    if assigned_price is None:
        return JsonResponse({
            "error": True,
            "message": _("Your account has no prices assigned yet. Contact the administrator to enable order requests."),
        }, status=403)
    precio_actual = float(assigned_price)

    carrito = request.session.get("carrito", {})

    if producto_id in carrito:

        carrito[producto_id]["cantidad"] += cantidad
        carrito[producto_id]["precio"] = precio_actual
        carrito[producto_id]["presentacion_id"] = presentacion_id
        carrito[producto_id]["producto_id"] = producto.id
    
    else:

        carrito[producto_id] = {
            "nombre": producto.nombre,
            "producto_id": producto.id,
            "presentacion_id": presentacion_id,
            "presentacion_nombre": presentacion.nombre,
            "precio": precio_actual,
            "cantidad": cantidad,
        }

    _reaplicar_promociones_carrito(carrito, cliente)
    carrito[producto_id]["subtotal"] = float(calcular_subtotal_item_pedido(
        precio=carrito[producto_id]["precio"],
        cantidad=carrito[producto_id]["cantidad"],
        descuento_aplicado=carrito[producto_id].get("descuento_aplicado", False),
        descuento_monto=carrito[producto_id].get("descuento_monto", 0),
    ))

    request.session["carrito"] = carrito

    total_items = sum(item["cantidad"] for item in carrito.values())

    return JsonResponse({
        "success": True,
        "total_items": total_items
    })
