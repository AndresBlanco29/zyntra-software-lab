from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q
from config.clientes.models import Cliente
from config.usuarios.models import Usuario
from config.productos.models import Producto, Presentacion, Categoria, Marca
from django.views.decorators.http import require_POST
import uuid
import json
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
import pytz
from django.contrib import messages
import logging
from django.contrib.auth.decorators import login_required
from decimal import Decimal, ROUND_HALF_UP

from config.pedidos.models import PedidoItem
from config.pedidos.services import crear_pedido_desde_items, notificar_backoffice_pedido
from config.usuarios.permissions import internal_permission_required
from config.usuarios.us_locations import US_STATE_CITIES, match_state_name, match_city_for_state


logger = logging.getLogger(__name__)


USA_COUNTRY_ALIASES = {'usa', 'us', 'eeuu', 'estados unidos', 'united states'}


def _money_decimal(value):
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _money_string(value):
    return format(_money_decimal(value), '.2f')


def _attach_recent_customer_order_history(*, cliente, productos):
    presentation_map = {}
    for producto in productos:
        for presentacion in producto.presentaciones.all():
            presentacion.recent_customer_orders = []
            presentation_map[presentacion.id] = presentacion

    if not presentation_map:
        return

    recent_items = (
        PedidoItem.objects
        .select_related('pedido')
        .filter(
            pedido__cliente=cliente,
            presentacion_id__in=presentation_map.keys(),
        )
        .exclude(pedido__estado='CANCELADO')
        .order_by('presentacion_id', '-pedido__creada_en', '-pedido_id', '-id')
    )

    history_counts = {presentacion_id: 0 for presentacion_id in presentation_map}
    for pedido_item in recent_items:
        presentacion_id = pedido_item.presentacion_id
        if history_counts[presentacion_id] >= 3:
            continue
        presentation_map[presentacion_id].recent_customer_orders.append(pedido_item)
        history_counts[presentacion_id] += 1


def _normalize_customer_location_payload(data):
    direccion = (data.get('direccion') or '').strip()
    ciudad = (data.get('ciudad') or '').strip()
    estado = (data.get('estado') or '').strip()
    codigo_postal = (data.get('codigo_postal') or '').strip()
    pais = (data.get('pais') or 'USA').strip()
    manual_location = bool(data.get('manual_location'))

    if not direccion:
        raise ValidationError(_('Address is required.'))
    if not ciudad:
        raise ValidationError(_('City is required.'))
    if not estado:
        raise ValidationError(_('State or province is required.'))
    if not pais:
        raise ValidationError(_('Country is required.'))

    normalized_country = pais.lower()
    is_usa = normalized_country in USA_COUNTRY_ALIASES

    if is_usa and not manual_location:
        matched_state = match_state_name(estado)
        if not matched_state:
            raise ValidationError(_('Select a valid state.'))

        matched_city = match_city_for_state(matched_state, ciudad)
        if not matched_city:
            raise ValidationError(_('Select a valid city for the chosen state.'))

        estado = matched_state
        ciudad = matched_city
        pais = 'USA'

    return {
        'direccion': direccion,
        'ciudad': ciudad,
        'estado': estado,
        'codigo_postal': codigo_postal,
        'pais': pais,
    }


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
            messages.error(request, _('Attach the tax certificate to create the customer.'))
            return render(request, "vendedores/crear_cliente.html")

        if not request.POST.get("confirmacion"):
            messages.error(request, _('Accept the statement confirming the tax information is accurate.'))
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

VENDEDOR_CLIENTES_PAGE_SIZE = 50
VENDEDOR_CATALOGO_PAGE_SIZE = 50


def _clientes_filter_params(request):
    params = {}
    query = str(request.GET.get('q') or '').strip()
    if query:
        params['q'] = query
    estado = str(request.GET.get('estado') or '').strip()
    if estado in ('activo', 'inactivo'):
        params['estado'] = estado
    return params


def _clientes_queryset(request):
    queryset = Cliente.objects.select_related('usuario').order_by('nombre_empresa', 'id')

    filters = _clientes_filter_params(request)
    query = filters.get('q')
    if query:
        queryset = queryset.filter(
            Q(nombre_empresa__icontains=query)
            | Q(usuario__first_name__icontains=query)
            | Q(usuario__last_name__icontains=query)
            | Q(usuario__email__icontains=query)
            | Q(usuario__username__icontains=query)
            | Q(telefono__icontains=query)
            | Q(sales_tax_number__icontains=query)
        )
    estado = filters.get('estado')
    if estado == 'activo':
        queryset = queryset.filter(aprobado=True)
    elif estado == 'inactivo':
        queryset = queryset.filter(aprobado=False)
    return queryset


def _tomar_pedido_clientes_filter_params(request):
    params = {}
    query = str(request.GET.get('q') or '').strip()
    if query:
        params['q'] = query
    return params


def _tomar_pedido_clientes_queryset(request):
    queryset = (
        Cliente.objects.filter(aprobado=True)
        .select_related('usuario')
        .order_by('nombre_empresa', 'id')
    )

    query = _tomar_pedido_clientes_filter_params(request).get('q')
    if query:
        queryset = queryset.filter(
            Q(nombre_empresa__icontains=query)
            | Q(usuario__first_name__icontains=query)
            | Q(usuario__last_name__icontains=query)
            | Q(usuario__email__icontains=query)
            | Q(usuario__username__icontains=query)
            | Q(telefono__icontains=query)
            | Q(sales_tax_number__icontains=query)
        )
    return queryset


def _catalogo_vendedor_filter_params(request):
    params = {}
    query = str(request.GET.get('q') or '').strip()
    if query:
        params['q'] = query
    categoria_id = str(request.GET.get('categoria') or '').strip()
    if categoria_id.isdigit():
        params['categoria'] = categoria_id
    marca_id = str(request.GET.get('marca') or '').strip()
    if marca_id.isdigit():
        params['marca'] = marca_id
    return params


def _catalogo_vendedor_queryset(request):
    queryset = (
        Producto.objects.filter(activo=True)
        .select_related('categoria', 'marca')
        .prefetch_related('presentaciones')
        .order_by('nombre', 'id')
    )

    filters = _catalogo_vendedor_filter_params(request)
    query = filters.get('q')
    if query:
        queryset = queryset.filter(
            Q(nombre__icontains=query)
            | Q(nombre_en__icontains=query)
            | Q(codigo_barras__icontains=query)
        )
    if filters.get('categoria'):
        queryset = queryset.filter(categoria_id=filters['categoria'])
    if filters.get('marca'):
        queryset = queryset.filter(marca_id=filters['marca'])
    return queryset


@login_required
@internal_permission_required('vendor.customers.view')
def clientes(request):
    filter_params = _clientes_filter_params(request)
    paginator = Paginator(_clientes_queryset(request), VENDEDOR_CLIENTES_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'clientes': page_obj.object_list,
        'page_obj': page_obj,
        'filter_params': filter_params,
        'filter_q': filter_params.get('q', ''),
        'filter_estado': filter_params.get('estado', ''),
        'us_locations_json': json.dumps(US_STATE_CITIES),
        'us_states': sorted(US_STATE_CITIES.keys()),
    }

    return render(request, 'vendedores/clientes.html', context)

@login_required
@internal_permission_required('vendor.orders.view', 'backoffice.orders.view')
def tomar_pedido(request):
    filter_params = _tomar_pedido_clientes_filter_params(request)
    paginator = Paginator(_tomar_pedido_clientes_queryset(request), VENDEDOR_CLIENTES_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'clientes': page_obj.object_list,
        'page_obj': page_obj,
        'filter_q': filter_params.get('q', ''),
    }

    return render(request, 'vendedores/tomar_pedido.html', context)

@login_required
@internal_permission_required('vendor.orders.view', 'backoffice.orders.view')
def catalogo_vendedor(request, cliente_id):

    request.session["cliente_id"] = cliente_id

    cliente = Cliente.objects.get(id=cliente_id)

    filter_params = _catalogo_vendedor_filter_params(request)
    paginator = Paginator(_catalogo_vendedor_queryset(request), VENDEDOR_CATALOGO_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    productos = list(page_obj.object_list)
    _attach_recent_customer_order_history(cliente=cliente, productos=productos)

    categorias = Categoria.objects.all()
    marcas = Marca.objects.filter(activo=True)

    carrito = request.session.get("pedido", {})

    total_items = sum(item["cantidad"] for item in carrito.values())

    total = sum(
        item["precio"] * item["cantidad"]
        for item in carrito.values()
    )

    context = {
        'cliente': cliente,
        'productos': productos,
        'page_obj': page_obj,
        'filter_q': filter_params.get('q', ''),
        'filter_categoria': filter_params.get('categoria', ''),
        'filter_marca': filter_params.get('marca', ''),
        'categorias': categorias,
        'marcas': marcas,
        'total_items': total_items,
        'total': total,
    }

    return render(request, 'vendedores/tomar_pedido_catalogo.html', context)

@login_required
@internal_permission_required('vendor.orders.manage', 'backoffice.orders.manage')
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
@internal_permission_required('vendor.orders.view', 'backoffice.orders.view')
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

        subtotal = _money_decimal(item["precio"] * item["cantidad"])

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
        "total": _money_decimal(total),
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
@internal_permission_required('vendor.orders.manage', 'backoffice.orders.manage')
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
@internal_permission_required('vendor.orders.manage', 'backoffice.orders.manage')
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

    subtotal = _money_decimal(cantidad * precio)

    # Recalcular total
    total = _money_decimal(sum(
        item["precio"] * item["cantidad"]
        for item in carrito.values()
    ))

    return JsonResponse({
        "cantidad": cantidad,
        "subtotal": _money_string(subtotal),
        "total": _money_string(total)
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
            "error": str(_('You must indicate how the order was taken.'))
    })

    if not carrito or not cliente_id:
        return JsonResponse({
            "success": False,
            "error": str(_('There are no selected products or customer to generate the order.'))
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

    try:
        pedido = crear_pedido_desde_items(
            cliente=cliente,
            items_payload=items_payload,
            origen='VENDEDOR',
            vendedor=request.user,
            nota_cliente=(request.POST.get('nota') or '').strip(),
            acepta_terminos=False,
            canal_toma=tipo_orden,
        )
    except ValidationError as exc:
        error_message = exc.messages[0] if getattr(exc, 'messages', None) else str(exc)
        return JsonResponse({
            "success": False,
            "error": error_message,
        }, status=409)

    warning = None

    try:
        notificar_backoffice_pedido(pedido)
    except Exception as exc:
        logger.exception("Error enviando notificacion del pedido %s: %s", pedido.id, exc)
        warning = str(_('The order was created, but the notification email could not be sent.'))

    request.session["pedido"] = {}
    request.session.pop("cliente_id", None)

    response = {"success": True, "pedido_id": pedido.id}
    if warning:
        response["warning"] = warning
    return JsonResponse(response)


@login_required
@require_POST
def editar_cliente(request):
    """Vista para editar los datos del cliente"""

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': _('Invalid request.')}, status=400)

    cliente_id = data.get('cliente_id')
    empresa = (data.get('empresa') or '').strip()
    correo = (data.get('correo') or '').strip()
    telefono = (data.get('telefono') or '').strip()

    if not cliente_id:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)
    if not empresa or not correo or not telefono:
        return JsonResponse({'success': False, 'message': _('Please complete all required fields.')}, status=400)
    if len(telefono) != 10 or not telefono.isdigit():
        return JsonResponse({'success': False, 'message': _('Phone number must contain exactly 10 digits.')}, status=400)

    try:
        location_payload = _normalize_customer_location_payload(data)
        cliente = Cliente.objects.select_related('usuario').get(id=cliente_id)

        cliente.nombre_empresa = empresa
        cliente.telefono = telefono
        cliente.direccion = location_payload['direccion']
        cliente.ciudad = location_payload['ciudad']
        cliente.estado = location_payload['estado']
        cliente.codigo_postal = location_payload['codigo_postal']
        cliente.pais = location_payload['pais']
        cliente.save(update_fields=['nombre_empresa', 'telefono', 'direccion', 'ciudad', 'estado', 'codigo_postal', 'pais'])

        cliente.usuario.email = correo
        cliente.usuario.save(update_fields=['email'])

        return JsonResponse({'success': True, 'message': _('Customer updated successfully.')})

    except Cliente.DoesNotExist:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)
    except ValidationError as exc:
        return JsonResponse({'success': False, 'message': exc.messages[0] if getattr(exc, 'messages', None) else str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=400)


@require_POST
def desactivar_cliente(request):
    """Desactiva un cliente y su usuario asociado."""
    
    # Solo administradores pueden desactivar clientes
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return JsonResponse({'success': False, 'message': str(_('Permission denied. Only administrators can deactivate customers.'))}, status=403)

    try:
        import json
        data = json.loads(request.body)
        cliente_id = data.get('cliente_id')

        if not cliente_id:
            return JsonResponse({'success': False, 'message': str(_('Customer ID is required.'))}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        cliente.aprobado = False
        cliente.save(update_fields=['aprobado'])

        cliente.usuario.is_active = False
        cliente.usuario.save(update_fields=['is_active'])

        return JsonResponse({'success': True, 'message': str(_('Customer deactivated successfully.'))})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


@require_POST
def activar_cliente(request):
    """Activa un cliente y su usuario asociado."""
    
    # Solo administradores pueden activar clientes
    if not (request.user.is_superuser or request.user.role == 'admin'):
        return JsonResponse({'success': False, 'message': str(_('Permission denied. Only administrators can activate customers.'))}, status=403)

    try:
        import json
        data = json.loads(request.body)
        cliente_id = data.get('cliente_id')

        if not cliente_id:
            return JsonResponse({'success': False, 'message': str(_('Customer ID is required.'))}, status=400)

        cliente = get_object_or_404(Cliente, id=cliente_id)
        cliente.aprobado = True
        cliente.save(update_fields=['aprobado'])

        cliente.usuario.is_active = True
        cliente.usuario.save(update_fields=['is_active'])

        return JsonResponse({'success': True, 'message': str(_('Customer activated successfully.'))})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)