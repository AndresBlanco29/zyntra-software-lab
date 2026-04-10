from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from config.clientes.models import Cliente
from config.usuarios.models import Usuario
from config.productos.models import Producto, Presentacion, Categoria, Marca
from django.views.decorators.http import require_POST
import uuid
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
import pytz
from django.contrib import messages
import logging
from django.contrib.auth.decorators import login_required

from config.pedidos.services import crear_pedido_desde_items, notificar_backoffice_pedido
from config.usuarios.permissions import internal_permission_required


logger = logging.getLogger(__name__)


@login_required
@internal_permission_required('vendor.customers.manage')
def crear_cliente(request):

    if request.method == "POST":

        # datos usuario
        nombre = request.POST.get("nombre")
        apellido = request.POST.get("apellido")
        email = request.POST.get("email")
        telefono = request.POST.get("telefono")

        # datos empresa
        empresa = request.POST.get("empresa")
        direccion = request.POST.get("direccion")
        ciudad = request.POST.get("ciudad")
        estado = request.POST.get("estado")
        sales_tax = request.POST.get("sales_tax")

        certificado = request.FILES.get("certificado")
        if not certificado:
            messages.error(request, "Debes adjuntar el certificado tax para crear el cliente.")
            return render(request, "vendedores/crear_cliente.html")

        if not request.POST.get("confirmacion"):
            messages.error(request, "Debes aceptar la declaración sobre la veracidad de la información fiscal.")
            return render(request, "vendedores/crear_cliente.html")

        # crear usuario
        username = f"user_{uuid.uuid4().hex[:8]}"

        usuario = Usuario.objects.create(
            username=username,
            first_name=nombre,
            last_name=apellido,
            email=email
        )

        # crear cliente
        Cliente.objects.create(
            usuario=usuario,
            nombre_empresa=empresa,
            telefono=telefono,
            direccion=direccion,
            ciudad=ciudad,
            estado=estado,
            sales_tax_number=sales_tax,
            certificado_tax=certificado,
            declaracion_fiscal_aceptada=True,
            declaracion_fiscal_aceptada_en=timezone.now(),
        )

        return redirect("vendedores_clientes")

    return render(request, "vendedores/crear_cliente.html")

@login_required
@internal_permission_required('vendor.customers.view')
def clientes(request):

    clientes = Cliente.objects.select_related('usuario').all()

    context = {
        "clientes": clientes
    }

    return render(request, "vendedores/clientes.html", context)

@login_required
@internal_permission_required('vendor.orders.view')
def tomar_pedido(request):

    clientes = Cliente.objects.filter(aprobado=True).select_related("usuario")

    context = {
        "clientes": clientes
    }

    return render(request, "vendedores/tomar_pedido.html", context)

@login_required
@internal_permission_required('vendor.orders.view')
def catalogo_vendedor(request, cliente_id):

    request.session["cliente_id"] = cliente_id

    cliente = Cliente.objects.get(id=cliente_id)

    productos = Producto.objects.filter(activo=True).prefetch_related("presentaciones")

    categorias = Categoria.objects.all()
    marcas = Marca.objects.filter(activo=True)

    carrito = request.session.get("pedido", {})

    total_items = sum(item["cantidad"] for item in carrito.values())

    total = sum(
        item["precio"] * item["cantidad"]
        for item in carrito.values()
    )

    context = {
        "cliente": cliente,
        "productos": productos,
        "categorias": categorias,
        "marcas": marcas,
        "total_items": total_items,
        "total": total
    }

    return render(request, "vendedores/tomar_pedido_catalogo.html", context)

@login_required
@internal_permission_required('vendor.orders.manage')
def agregar_producto_pedido(request):

    if request.method == "POST":

        presentacion_id = request.POST.get("presentacion_id")
        cantidad = int(request.POST.get("cantidad"))

        presentacion = Presentacion.objects.get(id=presentacion_id)

        carrito = request.session.get("pedido", {})

        precio = request.POST.get("precio")

        # Validación: rechazar si precio no está seleccionado
        if not precio or precio == "":
            return JsonResponse({
                "success": False,
                "error": "Debes seleccionar un precio antes de agregar el producto."
            }, status=400)

        precio = float(precio.replace(",", "."))

        if presentacion_id in carrito:

            carrito[presentacion_id]["cantidad"] += cantidad

        else:

            carrito[presentacion_id] = {
                "presentacion_id": presentacion_id,
                "producto_id": presentacion.producto.id,
                "nombre": presentacion.producto.nombre,
                "presentacion_nombre": presentacion.nombre,
                "precio": precio,
                "cantidad": cantidad
            }

        request.session["pedido"] = carrito

        total_items = sum(item["cantidad"] for item in carrito.values())
    
        total = sum(
            item["precio"] * item["cantidad"]
            for item in carrito.values()
        )

        return JsonResponse({
            "success": True,
            "total_items": total_items,
            "total": total
        })

@login_required
@internal_permission_required('vendor.orders.view')
def ver_pedido(request):

    carrito = request.session.get("pedido", {})

    cliente_id = request.session.get("cliente_id")

    cliente = None
    if cliente_id:
        cliente = Cliente.objects.get(id=cliente_id)

    total = 0
    productos = []

    for key, item in carrito.items():

        producto = Producto.objects.get(id=item["producto_id"])

        presentaciones = Presentacion.objects.filter(producto=producto)

        subtotal = item["precio"] * item["cantidad"]

        total += subtotal

        productos.append({
            "id": key,
            "nombre": item["nombre"],
            "presentacion_id": item["presentacion_id"],
            "precio": item["precio"],
            "cantidad": item["cantidad"],
            "subtotal": subtotal,
            "presentaciones": presentaciones
        })

    context = {
        "productos": productos,
        "total": total,
        "cliente": cliente,
        "cliente_id": cliente_id
    }

    print(carrito)

    return render(
        request,
        "vendedores/tomar_pedido_resumen.html",
        context
    )

@require_POST
@login_required
@internal_permission_required('vendor.orders.manage')
def eliminar_producto_pedido(request):

    producto_id = request.POST.get("producto_id")

    carrito = request.session.get("pedido", {})

    if producto_id in carrito:
        del carrito[producto_id]

    request.session["pedido"] = carrito

    total_items = sum(item["cantidad"] for item in carrito.values())

    return JsonResponse({
        "success": True,
        "total_items": total_items
    })


@require_POST
@require_POST
@login_required
@internal_permission_required('vendor.orders.manage')
def actualizar_cantidad_pedido(request):

    producto_id = request.POST.get("producto_id")
    accion = request.POST.get("accion")

    carrito = request.session.get("pedido", {})

    if producto_id in carrito:

        # SUMAR
        if accion == "sumar":
            carrito[producto_id]["cantidad"] += 1

        # RESTAR
        elif accion == "restar":
            if carrito[producto_id]["cantidad"] > 1:
                carrito[producto_id]["cantidad"] -= 1

        # ESCRIBIR CANTIDAD MANUAL
        elif accion == "set":
            cantidad = int(request.POST.get("cantidad", 1))
            if cantidad < 1:
                cantidad = 1
            carrito[producto_id]["cantidad"] = cantidad

        # CAMBIAR PRECIO
        elif accion == "cambiar_precio":

            precio = request.POST.get("precio")

            if precio:
                precio = precio.replace(",", ".")
                carrito[producto_id]["precio"] = float(precio)

        elif accion == "cambiar_presentacion":

            presentacion_id = request.POST.get("presentacion_id")

            if presentacion_id:
                presentacion = Presentacion.objects.get(id=presentacion_id)

                carrito[producto_id]["presentacion_id"] = presentacion_id
                carrito[producto_id]["presentacion_nombre"] = presentacion.nombre

    # Guardar sesión
    request.session["pedido"] = carrito

    cantidad = carrito[producto_id]["cantidad"]
    precio = carrito[producto_id]["precio"]

    subtotal = cantidad * precio

    # Recalcular total
    total = sum(
        item["precio"] * item["cantidad"]
        for item in carrito.values()
    )

    return JsonResponse({
        "cantidad": cantidad,
        "subtotal": subtotal,
        "total": total
    })

@login_required
@internal_permission_required('vendor.orders.manage')
def enviar_pedido(request):

    carrito = request.session.get("pedido", {})
    cliente_id = request.session.get("cliente_id")
    tipo_orden = request.POST.get("tipo_orden")

    if not tipo_orden:
        return JsonResponse({
            "success": False,
            "error": "Debe indicar cómo se tomó la orden"
    })

    if not carrito or not cliente_id:
        return JsonResponse({
            "success": False,
            "error": "No hay productos ni cliente seleccionados para generar el pedido."
        }, status=400)

    cliente = Cliente.objects.get(id=cliente_id)

    items_payload = []

    for item in carrito.values():

        presentacion = Presentacion.objects.get(id=item["presentacion_id"])
        items_payload.append({
            "presentacion": presentacion,
            "cantidad": item["cantidad"],
            "precio": item["precio"],
        })

    pedido = crear_pedido_desde_items(
        cliente=cliente,
        items_payload=items_payload,
        origen='VENDEDOR',
        vendedor=request.user,
        nota_cliente=(request.POST.get('nota') or '').strip(),
        acepta_terminos=False,
        canal_toma=tipo_orden,
    )

    warning = None

    try:
        notificar_backoffice_pedido(pedido)
    except Exception as exc:
        logger.exception("Error enviando notificacion del pedido %s: %s", pedido.id, exc)
        warning = "El pedido se generó, pero no se pudo enviar el correo de notificación."

    request.session["pedido"] = {}
    request.session.pop("cliente_id", None)

    response = {"success": True, "pedido_id": pedido.id}
    if warning:
        response["warning"] = warning
    return JsonResponse(response)


def editar_cliente(request):
    """Vista para editar los datos del cliente"""
    
    if request.method == 'POST':
        import json
        
        try:
            data = json.loads(request.body)
            cliente_id = data.get('cliente_id')
            empresa = data.get('empresa')
            correo = data.get('correo')
            telefono = data.get('telefono')
            
            # Obtener el cliente
            cliente = Cliente.objects.get(id=cliente_id)
            
            # Verificar que el vendedor es el que intenta editar
            # (puedes agregar validación si es necesario)
            
            # Actualizar datos del cliente
            cliente.nombre_empresa = empresa
            cliente.telefono = telefono
            cliente.save()
            
            # Actualizar email del usuario
            cliente.usuario.email = correo
            cliente.usuario.save()
            
            return JsonResponse({'success': True, 'message': 'Cliente actualizado correctamente'})
            
        except Cliente.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Cliente no encontrado'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
    
    return JsonResponse({'success': False, 'message': 'Método no permitido'}, status=400)


@require_POST
def desactivar_cliente(request):
    """Desactiva un cliente y su usuario asociado."""
    
    # Solo administradores pueden desactivar clientes
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return JsonResponse({'success': False, 'message': 'Permiso denegado. Solo administradores pueden desactivar clientes.'}, status=403)

    try:
        import json
        data = json.loads(request.body)
        cliente_id = data.get('cliente_id')

        if not cliente_id:
            return JsonResponse({'success': False, 'message': 'ID de cliente requerido'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        cliente.aprobado = False
        cliente.save(update_fields=['aprobado'])

        cliente.usuario.is_active = False
        cliente.usuario.save(update_fields=['is_active'])

        return JsonResponse({'success': True, 'message': 'Cliente desactivado correctamente'})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


@require_POST
def activar_cliente(request):
    """Activa un cliente y su usuario asociado."""
    
    # Solo administradores pueden activar clientes
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return JsonResponse({'success': False, 'message': 'Permiso denegado. Solo administradores pueden activar clientes.'}, status=403)

    try:
        import json
        data = json.loads(request.body)
        cliente_id = data.get('cliente_id')

        if not cliente_id:
            return JsonResponse({'success': False, 'message': 'ID de cliente requerido'}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        cliente.aprobado = True
        cliente.save(update_fields=['aprobado'])

        cliente.usuario.is_active = True
        cliente.usuario.save(update_fields=['is_active'])

        return JsonResponse({'success': True, 'message': 'Cliente activado correctamente'})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)