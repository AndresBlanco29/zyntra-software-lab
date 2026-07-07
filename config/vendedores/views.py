from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q
from config.clientes.models import Cliente
from config.clientes.assignment import filter_clientes_for_vendedor
from config.clientes.phone import normalize_stored_phone_number
from config.usuarios.models import Usuario
from config.productos.models import Producto, Presentacion, Categoria, Marca, ConfiguracionPrecios, ConfiguracionDescuentos
from config.productos.views import _hydrate_productos
from django.views.decorators.http import require_POST
import uuid
import json
import re
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
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from config.facturacion.services import annotate_clientes_open_invoice_balance, get_recent_customer_invoice_items_by_presentation
from config.pedidos.services import (
    calcular_precio_unitario_neto_item,
    calcular_subtotal_item_pedido,
    crear_pedido_desde_items,
    normalizar_descuento_item_pedido,
    notificar_backoffice_pedido,
)
from config.usuarios.permissions import internal_permission_required
from config.usuarios.us_locations import US_STATE_CITIES, match_state_name, match_city_for_state


logger = logging.getLogger(__name__)


USA_COUNTRY_ALIASES = {'usa', 'us', 'eeuu', 'estados unidos', 'united states'}


def _money_decimal(value):
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _money_string(value):
    return format(_money_decimal(value), '.2f')


def _cart_item_subtotal(item):
    return calcular_subtotal_item_pedido(
        precio=item.get('precio', 0),
        cantidad=item.get('cantidad', 0),
        descuento_aplicado=item.get('descuento_aplicado', False),
        descuento_monto=item.get('descuento_monto', 0),
    )


def _cart_total(carrito):
    return _money_decimal(sum(_cart_item_subtotal(item) for item in carrito.values()))


def _cart_item_pricing_payload(item):
    subtotal = _cart_item_subtotal(item)
    net_unit = calcular_precio_unitario_neto_item(
        precio=item.get('precio', 0),
        descuento_aplicado=item.get('descuento_aplicado', False),
        descuento_monto=item.get('descuento_monto', 0),
    )
    discount_enabled = bool(item.get('descuento_aplicado'))
    discount_amount = _money_decimal(item.get('descuento_monto', 0) if discount_enabled else 0)
    quantity = int(item.get('cantidad', 0) or 0)
    return {
        'subtotal': _money_string(subtotal),
        'net_unit_price': _money_string(net_unit),
        'discount_amount': _money_string(discount_amount),
        'line_savings': _money_string(discount_amount * quantity),
        'discount_applied': discount_enabled,
    }


def _normalize_precio_key(value):
    precio_key = str(value or '').strip().lower()
    if precio_key in {f'precio_{index}' for index in range(1, 6)}:
        return precio_key
    return ''


def _infer_precio_key(*, presentacion, precio):
    precio_decimal = _money_decimal(precio)
    for index in range(1, 6):
        tier_price = _money_decimal(getattr(presentacion, f'precio_{index}', 0) or 0)
        if tier_price == precio_decimal:
            return f'precio_{index}'
    return ''


def _resolve_cart_item_price(*, presentacion, precio, precio_key=''):
    normalized_key = _normalize_precio_key(precio_key)
    if normalized_key:
        tier_price = _money_decimal(getattr(presentacion, normalized_key, 0) or 0)
        if tier_price > 0:
            return float(tier_price), normalized_key

    inferred_key = _infer_precio_key(presentacion=presentacion, precio=precio)
    if inferred_key:
        tier_price = _money_decimal(getattr(presentacion, inferred_key, 0) or 0)
        return float(tier_price), inferred_key

    return float(_money_decimal(precio)), ''


def _attach_recent_customer_order_history(*, cliente, productos):
    presentation_map = {}
    for producto in productos:
        for presentacion in producto.presentaciones.all():
            presentacion.recent_customer_sales = []
            presentation_map[presentacion.id] = presentacion

    if not presentation_map:
        return

    history_by_presentation = get_recent_customer_invoice_items_by_presentation(
        cliente=cliente,
        presentation_ids=presentation_map.keys(),
    )
    for presentacion_id, recent_items in history_by_presentation.items():
        presentation_map[presentacion_id].recent_customer_sales = recent_items


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
        telefono = normalize_stored_phone_number(telefono)

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
        cliente_kwargs = {
            'usuario': usuario,
            'nombre_empresa': empresa,
            'telefono': telefono,
            'direccion': direccion,
            'ciudad': ciudad,
            'estado': estado,
            'sales_tax_number': sales_tax,
            'certificado_tax': certificado,
            'declaracion_fiscal_aceptada': True,
            'declaracion_fiscal_aceptada_en': timezone.now(),
        }
        if getattr(request.user, 'role', '') == 'vendedor':
            cliente_kwargs['vendedor_asignado'] = request.user
            cliente_kwargs['vendedor_asignado_en'] = timezone.now()
            cliente_kwargs['vendedor_asignado_por'] = request.user
        Cliente.objects.create(**cliente_kwargs)

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
    queryset = annotate_clientes_open_invoice_balance(
        Cliente.objects.select_related('usuario').order_by('nombre_empresa', 'id')
    )

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
    return filter_clientes_for_vendedor(queryset, request.user)


def _build_catalog_bulk_price_options():
    return [
        {
            'key': f'precio_{index}',
            'label': _('Price %(number)s (%(percentage)s%%)') % {
                'number': index,
                'percentage': margin,
            },
        }
        for index, margin in enumerate(ConfiguracionPrecios.obtener().porcentajes_lista(), start=1)
    ]


def _build_order_summary_discount_preset_options():
    return ConfiguracionDescuentos.obtener().opciones_activas()


def _match_discount_preset_key(discount_options, current_amount):
    current = format(_money_decimal(current_amount or 0), '.2f')
    for option in discount_options:
        if option['value'] == current:
            return option['key']
    return ''


def _tomar_pedido_clientes_filter_params(request):
    params = {}
    raw_query = str(request.GET.get('q') or '')
    if raw_query:
        params['q'] = raw_query
    return params


def _tomar_pedido_clientes_queryset(request):
    queryset = (
        Cliente.objects.filter(aprobado=True)
        .select_related('usuario')
        .order_by('nombre_empresa', 'id')
    )

    query = (_tomar_pedido_clientes_filter_params(request).get('q') or '').strip()
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
    return filter_clientes_for_vendedor(queryset, request.user)


def _catalogo_vendedor_filter_params(request):
    params = {}
    raw_query = str(request.GET.get('q') or '')
    if raw_query:
        params['q'] = raw_query
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
    query = (filters.get('q') or '').strip()
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

    cliente = get_object_or_404(
        filter_clientes_for_vendedor(Cliente.objects.filter(id=cliente_id), request.user)
    )

    filter_params = _catalogo_vendedor_filter_params(request)
    paginator = Paginator(_catalogo_vendedor_queryset(request), VENDEDOR_CATALOGO_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    productos = _hydrate_productos(list(page_obj.object_list))
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
        'bulk_price_options': _build_catalog_bulk_price_options(),
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
        precio_key = _normalize_precio_key(request.POST.get("precio_key"))

        # Validación: rechazar si precio no está seleccionado
        if not precio or precio == "":
            return JsonResponse({
                "success": False,
                "error": "Debes seleccionar un precio antes de agregar el producto."
            }, status=400)

        precio, precio_key = _resolve_cart_item_price(
            presentacion=presentacion,
            precio=precio,
            precio_key=precio_key,
        )

        if presentacion_id in carrito:
            carrito[presentacion_id]["cantidad"] += cantidad
            carrito[presentacion_id]["precio"] = precio
            if precio_key:
                carrito[presentacion_id]["precio_key"] = precio_key
        else:
            carrito_item = {
                "presentacion_id": presentacion_id,
                "producto_id": presentacion.producto.id,
                "nombre": presentacion.producto.nombre,
                "presentacion_nombre": presentacion.nombre,
                "precio": precio,
                "cantidad": cantidad,
            }
            if precio_key:
                carrito_item["precio_key"] = precio_key
            carrito[presentacion_id] = carrito_item

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
    discount_options = _build_order_summary_discount_preset_options()

    for key, item in carrito.items():

        producto = Producto.objects.get(id=item["producto_id"])

        presentaciones = Presentacion.objects.filter(producto=producto)
        presentacion = Presentacion.objects.get(id=item["presentacion_id"])
        precio, precio_key = _resolve_cart_item_price(
            presentacion=presentacion,
            precio=item.get("precio", 0),
            precio_key=item.get("precio_key", ''),
        )
        if precio_key and item.get("precio_key") != precio_key:
            carrito[key]["precio_key"] = precio_key
        if float(item.get("precio", 0) or 0) != precio:
            carrito[key]["precio"] = precio

        subtotal = _cart_item_subtotal(item)

        total += subtotal

        productos.append({
            "id": key,
            "nombre": item["nombre"],
            "presentacion_id": item["presentacion_id"],
            "precio": precio,
            "precio_key": precio_key,
            "cantidad": item["cantidad"],
            "descuento_aplicado": bool(item.get("descuento_aplicado")),
            "descuento_monto": _money_decimal(item.get("descuento_monto", 0) if item.get("descuento_aplicado") else 0),
            "selected_discount_preset_key": (
                _match_discount_preset_key(discount_options, item.get("descuento_monto", 0))
                if item.get("descuento_aplicado")
                else ''
            ),
            "precio_neto": calcular_precio_unitario_neto_item(
                precio=precio,
                descuento_aplicado=item.get("descuento_aplicado", False),
                descuento_monto=item.get("descuento_monto", 0),
            ),
            "ahorro_linea": _money_decimal(
                (_money_decimal(item.get("descuento_monto", 0) if item.get("descuento_aplicado") else 0))
                * int(item.get("cantidad", 0) or 0)
            ),
            "subtotal": subtotal,
            "presentaciones": presentaciones
        })

    if carrito:
        request.session["pedido"] = carrito
        request.session.modified = True

    context = {
        "productos": productos,
        "total": _money_decimal(total),
        "cliente": cliente,
        "cliente_id": cliente_id,
        "bulk_price_options": _build_catalog_bulk_price_options(),
        "discount_preset_options": _build_order_summary_discount_preset_options(),
    }

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
            precio_key = _normalize_precio_key(request.POST.get("precio_key"))

            if precio:
                presentacion = Presentacion.objects.get(id=carrito[producto_id]["presentacion_id"])
                resolved_precio, resolved_key = _resolve_cart_item_price(
                    presentacion=presentacion,
                    precio=precio,
                    precio_key=precio_key,
                )
                carrito[producto_id]["precio"] = resolved_precio
                if resolved_key:
                    carrito[producto_id]["precio_key"] = resolved_key

        elif accion == "cambiar_presentacion":

            presentacion_id = request.POST.get("presentacion_id")

            if presentacion_id:
                presentacion = Presentacion.objects.get(id=presentacion_id)
                current_key = _normalize_precio_key(carrito[producto_id].get("precio_key"))
                if not current_key:
                    current_key = _infer_precio_key(
                        presentacion=Presentacion.objects.get(id=carrito[producto_id]["presentacion_id"]),
                        precio=carrito[producto_id]["precio"],
                    )

                carrito[producto_id]["presentacion_id"] = presentacion_id
                carrito[producto_id]["presentacion_nombre"] = presentacion.nombre

                resolved_precio, resolved_key = _resolve_cart_item_price(
                    presentacion=presentacion,
                    precio=carrito[producto_id]["precio"],
                    precio_key=current_key,
                )
                carrito[producto_id]["precio"] = resolved_precio
                if resolved_key:
                    carrito[producto_id]["precio_key"] = resolved_key

        elif accion == "cambiar_descuento":
            aplicado = request.POST.get("descuento_aplicado") == "1"
            monto = request.POST.get("descuento_monto", "0")
            aplicado, monto = normalizar_descuento_item_pedido(
                precio=carrito[producto_id]["precio"],
                descuento_aplicado=aplicado,
                descuento_monto=monto,
            )
            carrito[producto_id]["descuento_aplicado"] = aplicado
            carrito[producto_id]["descuento_monto"] = float(monto)

    # Guardar sesión
    request.session["pedido"] = carrito

    cantidad = carrito[producto_id]["cantidad"]
    pricing = _cart_item_pricing_payload(carrito[producto_id])
    total = _cart_total(carrito)

    return JsonResponse({
        "cantidad": cantidad,
        "subtotal": pricing["subtotal"],
        "net_unit_price": pricing["net_unit_price"],
        "discount_amount": pricing["discount_amount"],
        "line_savings": pricing["line_savings"],
        "discount_applied": pricing["discount_applied"],
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
            "descuento_aplicado": item.get("descuento_aplicado", False),
            "descuento_monto": item.get("descuento_monto", 0),
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
            bypass_stock_check=True,
            reservar_inventario=False,
            request=request,
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
    telefono = normalize_stored_phone_number(data.get('telefono'))

    if not cliente_id:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)
    if not empresa or not correo or not telefono:
        return JsonResponse({'success': False, 'message': _('Please complete all required fields.')}, status=400)
    if len(telefono) != 10 or not telefono.isdigit():
        return JsonResponse({'success': False, 'message': _('Phone number must contain exactly 10 digits.')}, status=400)

    try:
        location_payload = _normalize_customer_location_payload(data)
        try:
            cliente = filter_clientes_for_vendedor(
                Cliente.objects.select_related('usuario'),
                request.user,
            ).get(id=cliente_id)
        except Cliente.DoesNotExist:
            return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)

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


def _validate_customer_access_password(password):
    if not re.search(r'[A-Z]', password):
        return _('Password must include at least one uppercase letter.')
    if not re.search(r'[a-z]', password):
        return _('Password must include at least one lowercase letter.')
    if not re.search(r'\d', password):
        return _('Password must include at least one number.')
    if not re.search(r'[^A-Za-z0-9]', password):
        return _('Password must include at least one special character.')
    return None


@login_required
@require_POST
def configurar_terminos_cliente(request):
    """Save payment terms for a customer."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': _('Invalid request.')}, status=400)

    cliente_id = data.get('cliente_id')
    terminos_pago = str(data.get('terminos_pago') or '').strip().upper()

    if not cliente_id:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)

    valid_terms = {choice[0] for choice in Cliente.PAYMENT_TERMS_CHOICES}
    if terminos_pago not in valid_terms:
        return JsonResponse({'success': False, 'message': _('Please select a valid payment term.')}, status=400)

    try:
        cliente = filter_clientes_for_vendedor(
            Cliente.objects.all(),
            request.user,
        ).get(id=cliente_id)
    except Cliente.DoesNotExist:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)

    cliente.terminos_pago = terminos_pago
    cliente.save(update_fields=['terminos_pago'])

    return JsonResponse({
        'success': True,
        'message': _('Payment terms updated successfully.'),
        'terminos_pago': cliente.terminos_pago,
        'terminos_pago_label': cliente.get_terminos_pago_label(),
    })


@login_required
@require_POST
def configurar_limite_credito_cliente(request):
    """Save the maximum due balance allowed for a customer."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': _('Invalid request.')}, status=400)

    cliente_id = data.get('cliente_id')
    raw_limit = data.get('credit_limit')

    if not cliente_id:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)

    try:
        cliente = filter_clientes_for_vendedor(
            Cliente.objects.all(),
            request.user,
        ).get(id=cliente_id)
    except Cliente.DoesNotExist:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)

    if raw_limit in (None, ''):
        cliente.credit_limit = None
    else:
        try:
            credit_limit = Decimal(str(raw_limit)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            return JsonResponse({'success': False, 'message': _('Enter a valid credit limit amount.')}, status=400)
        if credit_limit < 0:
            return JsonResponse({'success': False, 'message': _('Credit limit cannot be negative.')}, status=400)
        cliente.credit_limit = credit_limit

    cliente.save(update_fields=['credit_limit'])

    remaining_limit = cliente.get_credit_limit_remaining()
    return JsonResponse({
        'success': True,
        'message': _('Credit limit updated successfully.'),
        'credit_limit': str(cliente.credit_limit) if cliente.credit_limit is not None else '',
        'remaining_limit': str(remaining_limit) if remaining_limit is not None else '',
        'due_balance': str(cliente.total_amount_owed),
    })


@login_required
@internal_permission_required('vendor.customers.manage')
@require_POST
def configurar_acceso_cliente(request):
    """Assign username and password to imported customers without web access."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': _('Invalid request.')}, status=400)

    cliente_id = data.get('cliente_id')
    username = str(data.get('username') or '').strip().lower()
    password = data.get('password') or ''
    password_confirm = data.get('password_confirm') or ''

    if not cliente_id:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)
    if not username:
        return JsonResponse({'success': False, 'message': _('Username is required.')}, status=400)
    if not password or not password_confirm:
        return JsonResponse({'success': False, 'message': _('Please enter and confirm the password.')}, status=400)
    if password != password_confirm:
        return JsonResponse({'success': False, 'message': _('Passwords do not match.')}, status=400)

    password_rule_error = _validate_customer_access_password(password)
    if password_rule_error:
        return JsonResponse({'success': False, 'message': password_rule_error}, status=400)

    try:
        cliente = filter_clientes_for_vendedor(
            Cliente.objects.select_related('usuario'),
            request.user,
        ).get(id=cliente_id)
    except Cliente.DoesNotExist:
        return JsonResponse({'success': False, 'message': _('Customer not found.')}, status=404)

    usuario = cliente.usuario
    if usuario.has_usable_password():
        return JsonResponse(
            {'success': False, 'message': _('This customer already has web access configured.')},
            status=400,
        )

    if Usuario.objects.filter(username=username).exclude(pk=usuario.pk).exists():
        return JsonResponse({'success': False, 'message': _('This username is already in use.')}, status=400)

    try:
        validate_password(password, usuario)
    except ValidationError as exc:
        message = exc.messages[0] if getattr(exc, 'messages', None) else str(exc)
        return JsonResponse({'success': False, 'message': message}, status=400)

    with transaction.atomic():
        usuario.username = username
        usuario.set_password(password)
        if not usuario.is_active:
            usuario.is_active = True
        usuario.save(update_fields=['username', 'password', 'is_active'])

        update_fields = []
        if not cliente.aprobado:
            cliente.aprobado = True
            update_fields.append('aprobado')
        if update_fields:
            cliente.save(update_fields=update_fields)

    return JsonResponse({'success': True, 'message': _('Web access configured successfully.')})