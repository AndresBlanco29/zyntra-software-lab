import functools
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.clientes.assignment import filter_clientes_for_vendedor
from config.clientes.models import Cliente
from config.notificaciones.models import crear_notificacion_backoffice
from config.pedidos.models import Pedido
from config.pedidos.client_history import (
    list_cliente_purchase_orders,
    merge_pedido_into_session_cart,
)
from config.facturacion.services import get_recent_customer_invoice_items_by_presentation
from config.pedidos.services import (
    calcular_precio_unitario_neto_item,
    calcular_subtotal_item_pedido,
    crear_pedido_desde_items,
    normalizar_descuento_item_pedido,
    notificar_backoffice_pedido,
    notificar_cliente_pedido,
)
from config.productos.models import ConfiguracionDescuentos, ConfiguracionPrecios, Presentacion
from config.productos.promotions import (
    aplicar_promocion_en_item_sesion,
    asegurar_promociones_en_cotizacion,
    estado_promocion_para_linea,
    reaplicar_promociones_en_lineas_sesion,
)
from config.usuarios.permissions import internal_permission_required, user_has_permission

from .models import Cotizacion, CotizacionItem
from .services import (
    cliente_tiene_email as _cliente_tiene_email_registrado,
    notificar_backoffice_solicitud_cotizacion,
    notificar_cliente_solicitud_cotizacion,
    user_can_manage_cotizacion,
    user_can_view_cotizacion,
)
from django.utils.translation import gettext as _


logger = logging.getLogger(__name__)

BACKOFFICE_QUOTES_PAGE_SIZE = 50

QUOTE_SEND_READY_SESSION_KEY = 'backoffice_quote_send_ready'
MIN_BACKOFFICE_QUOTE_PRICE = Decimal('1.00')


def _quote_send_ready_map(session):
    return dict(session.get(QUOTE_SEND_READY_SESSION_KEY, {}))


def _set_quote_send_ready(session, cotizacion_id, ready):
    ready_map = _quote_send_ready_map(session)
    key = str(cotizacion_id)
    if ready:
        ready_map[key] = True
    else:
        ready_map.pop(key, None)
    session[QUOTE_SEND_READY_SESSION_KEY] = ready_map
    session.modified = True


def _is_quote_send_ready(session, cotizacion_id):
    return bool(_quote_send_ready_map(session).get(str(cotizacion_id)))


def _is_backoffice_user(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.role in {'admin', 'backoffice'}))


def _parse_decimal(value, default='0'):
    text = str(value if value is not None else default).strip().replace(',', '.')
    if not text:
        text = str(default)
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _parse_quantity(value, default=1):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        quantity = default
    return max(quantity, 1)


from config.core.profit import build_order_line_profit, summarize_order_profit


def _default_backoffice_quote_price(item, cotizacion):
    return _parse_decimal(item.precio, 0)


def _quote_item_price_for_customer(*, cliente, presentacion, session_price):
    session_price_decimal = _parse_decimal(session_price, 0)
    if not cliente or cliente.estado_revision != Cliente.REVIEW_STATUS_APPROVED:
        return session_price_decimal

    assigned_customer_tier = cliente.get_nivel_precio_normalizado()
    if assigned_customer_tier is None:
        return session_price_decimal

    assigned_price = _parse_decimal(
        presentacion.get_price_for_tier(assigned_customer_tier),
        session_price_decimal,
    )
    if assigned_price > 0:
        return assigned_price

    return session_price_decimal


def _validate_backoffice_quote_price(*, item=None, presentacion=None, price):
    presentation = presentacion or (item.presentacion if item else None)
    if presentation is None:
        return _('Unable to validate the product price.')
    product_label = f'{presentation.producto.nombre} ({presentation.nombre})'
    cost = presentation.costo

    if price <= MIN_BACKOFFICE_QUOTE_PRICE:
        return _('The price for %(product)s must be greater than $1.00.') % {
            'product': product_label,
        }

    if cost is not None:
        cost_decimal = _parse_decimal(cost, 0)
        if price < cost_decimal:
            return _('The price for %(product)s cannot be lower than cost ($%(cost)s) because it would generate a loss.') % {
                'product': product_label,
                'cost': cost_decimal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
            }

    return ''


def _build_quote_discount_preset_options():
    return ConfiguracionDescuentos.obtener().opciones_activas()


def _match_discount_preset_key(discount_options, current_amount):
    current = format(_parse_decimal(current_amount, 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), '.2f')
    for option in discount_options:
        if option['value'] == current:
            return option['key']
    return ''


def _build_quote_item_rows(cotizacion):
    margin_values = ConfiguracionPrecios.obtener().porcentajes_lista()
    discount_options = _build_quote_discount_preset_options()
    rows = []
    display_total = Decimal('0.00')
    quote_items = list(cotizacion.items.select_related('presentacion__producto'))
    recent_orders_by_presentation = get_recent_customer_invoice_items_by_presentation(
        cliente=cotizacion.cliente,
        presentation_ids=[item.presentacion_id for item in quote_items],
    )

    for item in quote_items:
        current_price = _default_backoffice_quote_price(item, cotizacion)
        display_subtotal = calcular_subtotal_item_pedido(
            precio=current_price,
            cantidad=item.cantidad,
            descuento_aplicado=item.descuento_aplicado,
            descuento_monto=item.descuento_monto,
        )
        net_unit_price = calcular_precio_unitario_neto_item(
            precio=current_price,
            descuento_aplicado=item.descuento_aplicado,
            descuento_monto=item.descuento_monto,
        )
        discount_line_total = (
            _parse_decimal(item.descuento_monto, 0) * Decimal(str(item.cantidad or 0))
            if item.descuento_aplicado
            else Decimal('0.00')
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        price_options = []
        selected_option = ''

        for index, margin in enumerate(margin_values, start=1):
            option_key = f'precio_{index}'
            option_value = _parse_decimal(getattr(item.presentacion, option_key), 0)
            if option_value == current_price:
                selected_option = option_key
            price_options.append({
                'key': option_key,
                'label': _('Price %(number)s (%(percentage)s%%)') % {
                    'number': index,
                    'percentage': margin,
                },
                'value': option_value,
                'margin': margin,
            })

        if item.presentacion.qb_price is not None:
            qb_value = _parse_decimal(item.presentacion.qb_price, 0)
            if qb_value == current_price:
                selected_option = 'qb_price'
            price_options.append({
                'key': 'qb_price',
                'label': 'QB-PRICE',
                'value': qb_value,
                'margin': None,
            })

        profit = build_order_line_profit(
            cost=item.presentacion.costo,
            list_price=current_price,
            quantity=item.cantidad,
            descuento_aplicado=item.descuento_aplicado,
            descuento_monto=item.descuento_monto,
        )
        rows.append({
            'item': item,
            'price_options': price_options,
            'selected_option': selected_option,
            'current_price': current_price,
            'display_subtotal': display_subtotal,
            'precio_unitario_neto': net_unit_price,
            'descuento_linea_total': discount_line_total,
            'selected_discount_preset_key': (
                _match_discount_preset_key(discount_options, item.descuento_monto)
                if item.descuento_aplicado
                else ''
            ),
            'cost': item.presentacion.costo,
            'minimum_price': max(
                (_parse_decimal(item.presentacion.costo, 0) if item.presentacion.costo is not None else Decimal('0.00')),
                Decimal('1.01'),
            ),
            'profit': profit,
            'current_utility_percentage': profit.get('profit_percent'),
            'recent_customer_sales': recent_orders_by_presentation.get(item.presentacion_id, []),
        })
        display_total += display_subtotal

    return rows, display_total, summarize_order_profit([row['profit'] for row in rows])


def _build_bulk_quote_price_options():
    options = [
        {
            'key': f'precio_{index}',
            'label': _('Price %(number)s (%(percentage)s%%)') % {
                'number': index,
                'percentage': margin,
            },
        }
        for index, margin in enumerate(ConfiguracionPrecios.obtener().porcentajes_lista(), start=1)
    ]
    options.append({
        'key': 'qb_price',
        'label': 'QB-PRICE',
    })
    return options


def _get_generated_order_from_quote(cotizacion):
    try:
        return cotizacion.pedido_generado
    except Pedido.DoesNotExist:
        return None


def _puede_anular_cotizacion_desde_backoffice(cotizacion):
    return cotizacion.estado not in {'CANCELADA_CLIENTE', 'RECHAZADA'}


def _puede_eliminar_cotizacion_desde_backoffice(cotizacion):
    return _get_generated_order_from_quote(cotizacion) is None


@transaction.atomic
def _anular_cotizacion_desde_backoffice(*, cotizacion):
    if not _puede_anular_cotizacion_desde_backoffice(cotizacion):
        raise ValidationError(_('This quote cannot be voided.'))
    cotizacion.estado = 'CANCELADA_CLIENTE'
    cotizacion.save(update_fields=['estado'])
    return cotizacion


@transaction.atomic
def _eliminar_cotizacion_desde_backoffice(*, cotizacion):
    if not _puede_eliminar_cotizacion_desde_backoffice(cotizacion):
        raise ValidationError(_('This quote cannot be deleted because a sales order was already generated from it.'))
    cotizacion.delete()


def _build_order_items_payload_from_quote(cotizacion):
    items_payload = []
    for item in cotizacion.items.select_related('presentacion__producto'):
        items_payload.append({
            'presentacion': item.presentacion,
            'cantidad': item.cantidad,
            'precio': item.precio,
            'descuento_aplicado': item.descuento_aplicado,
            'descuento_monto': item.descuento_monto,
        })
    return items_payload


def _create_purchase_order_from_quote(*, cotizacion, items_payload, nota_cliente, origen, canal_toma, acepta_terminos):
    pedido = crear_pedido_desde_items(
        cliente=cotizacion.cliente,
        items_payload=items_payload,
        origen=origen,
        vendedor=cotizacion.vendedor,
        cotizacion=cotizacion,
        nota_cliente=nota_cliente,
        acepta_terminos=acepta_terminos,
        canal_toma=canal_toma,
        bypass_stock_check=True,
        reservar_inventario=False,
    )

    cotizacion.estado = 'CONFIRMADA_CLIENTE'
    cotizacion.total = pedido.total
    cotizacion.nota_confirmacion_cliente = nota_cliente
    cotizacion.save(update_fields=['estado', 'total', 'nota_confirmacion_cliente'])
    return pedido


def _cliente_from_user(user):
    return get_object_or_404(Cliente.objects.select_related('usuario'), usuario=user)


def _redirect_to_home_login(request):
    next_url = quote(request.get_full_path(), safe='')
    return redirect(f"{reverse('home')}?show_login=1&next={next_url}")


def _cotizaciones_pendientes_cliente(cliente):
    return Cotizacion.objects.filter(cliente=cliente, estado='LISTA_PARA_CONFIRMACION').count()


def _cliente_pedido_estado_label(state):
    return {
        'RECIBIDO': _('Received'),
        'EN_GESTION': _('In progress'),
        'LISTO_PARA_PICKING': _('Ready for picking'),
        'PARA_VERIFICAR': _('Pending verification'),
        'VERIFICADO_AJUSTADO': _('Verified and adjusted'),
        'INVOICE_GENERADA': _('Invoice generated'),
        'DESPACHADO': _('Dispatched'),
        'CANCELADO': _('Cancelled'),
    }.get(state, state)


def _build_confirm_url(request, cotizacion):
    path = reverse('cliente_cotizacion_recibida_detalle', args=[str(cotizacion.token_cliente)])
    if settings.APP_BASE_URL:
        return f"{settings.APP_BASE_URL}{path}"
    return request.build_absolute_uri(path)


def _normalize_phone(phone_number):
    digits = ''.join(character for character in str(phone_number or '') if character.isdigit())
    if len(digits) == 10:
        digits = f'1{digits}'
    return digits


def _build_quote_message(cotizacion, confirm_url):
    return (
        f"Hola {cotizacion.cliente.nombre_empresa}, tu pedido #{cotizacion.id} "
        f"ya esta listo para confirmacion. Ingresa aqui: {confirm_url}"
    )


def _build_whatsapp_link(phone_number, message):
    normalized = _normalize_phone(phone_number)
    if not normalized:
        return ''
    return f"https://wa.me/{normalized}?text={quote(message)}"


def _send_twilio_message(*, phone_number, message, channel):
    if not phone_number or not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        return False

    try:
        from twilio.rest import Client
    except Exception:
        logger.exception('Twilio no esta disponible para envio de %s.', channel)
        return False

    from_number = settings.TWILIO_SMS_FROM if channel == 'sms' else settings.TWILIO_WHATSAPP_FROM
    if not from_number:
        return False

    destination = f'+{phone_number}'
    sender = from_number

    if channel == 'whatsapp':
        destination = f'whatsapp:+{phone_number}'
        if not sender.startswith('whatsapp:'):
            sender = f'whatsapp:{sender}'

    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    client.messages.create(body=message, from_=sender, to=destination)
    return True


def _email_includes_prices(request):
    return request.POST.get('enviar_correo_con_precios', '0') == '1'


def _cliente_tiene_email(cliente):
    email = getattr(getattr(cliente, 'usuario', None), 'email', '') or ''
    return bool(email.strip())


def _get_whatsapp_contact_data(cotizacion, request):
    confirm_url = _build_confirm_url(request, cotizacion)
    message = _build_quote_message(cotizacion, confirm_url)
    phone_number = _normalize_phone(cotizacion.cliente.telefono or cotizacion.cliente.usuario.telefono)
    whatsapp_link = _build_whatsapp_link(phone_number, message)
    return confirm_url, phone_number, whatsapp_link, message


def agregar_a_cotizacion(request):

    if request.method == "POST":
        presentacion_id = request.POST.get("presentacion_id")
        cantidad = int(request.POST.get("cantidad"))
        ensure_promo_minimum = request.POST.get("promo_minimum") == "1"

        presentacion = Presentacion.objects.get(id=presentacion_id)
        producto = presentacion.producto
        cliente = None
        if getattr(request.user, 'is_authenticated', False):
            cliente = Cliente.objects.filter(usuario=request.user).first()
        precio = _quote_item_price_for_customer(
            cliente=cliente,
            presentacion=presentacion,
            session_price=0,
        )

        carrito = request.session.get("carrito", {})

        key = str(presentacion_id)

        if key in carrito:
            if ensure_promo_minimum:
                carrito[key]["cantidad"] = max(carrito[key]["cantidad"], cantidad)
            else:
                carrito[key]["cantidad"] += cantidad
            carrito[key]["precio"] = float(precio)
        else:
            carrito[key] = {
                "producto_id": producto.id,
                "presentacion_id": presentacion.id,
                "nombre": producto.nombre,
                "cantidad": cantidad,
                "precio": float(precio),
            }

        carrito[key]["producto_id"] = producto.id
        # Re-evaluate every line so combo promotions can sum quantities across
        # the different products that make up the combo.
        reaplicar_promociones_en_lineas_sesion(carrito, cliente=cliente)

        request.session["carrito"] = carrito

        total_items = sum(item["cantidad"] for item in carrito.values())

        return JsonResponse({
            "success": True,
            "total_items": total_items
        })


@login_required
def ver_cotizacion(request):

    carrito_session = request.session.get("carrito", {})
    carrito = []
    cliente = _cliente_from_user(request.user)

    # First pass: resolve current price and product id for every line so that
    # combo promotions can correctly sum quantities across all of them.
    presentaciones_cache = {}
    for presentacion_id, item in list(carrito_session.items()):
        try:
            presentacion = Presentacion.objects.get(id=presentacion_id)
        except Presentacion.DoesNotExist:
            del carrito_session[presentacion_id]
            continue

        presentaciones_cache[presentacion_id] = presentacion
        precio = _quote_item_price_for_customer(
            cliente=cliente,
            presentacion=presentacion,
            session_price=item.get("precio", 0),
        )
        item["producto_id"] = presentacion.producto_id
        item["presentacion_id"] = presentacion.id
        item["precio"] = float(precio)

    # Apply promotions across ALL lines together (combo aggregation).
    reaplicar_promociones_en_lineas_sesion(carrito_session, cliente=cliente)
    lineas_context = list(carrito_session.values())

    for presentacion_id, item in list(carrito_session.items()):
        presentacion = presentaciones_cache.get(presentacion_id)
        if presentacion is None:
            continue
        producto = presentacion.producto
        precio = item.get("precio", 0)
        promocion_estado = estado_promocion_para_linea(
            producto_id=producto.id,
            presentacion_id=presentacion.id,
            cantidad=item["cantidad"],
            precio_unitario=precio,
            presentacion=presentacion,
            cliente=cliente,
            lineas_context=lineas_context,
        )

        carrito.append({
            "id": presentacion_id,
            "producto": producto,
            "presentacion": presentacion,
            "cantidad": item["cantidad"],
            "descuento_aplicado": bool(item.get("descuento_aplicado")),
            "descuento_monto": item.get("descuento_monto", 0),
            "descuento_origen": item.get("descuento_origen") or "",
            "promocion_nombre": item.get("promocion_nombre") or "",
            "promocion_descripcion": item.get("promocion_descripcion") or "",
            "promocion_estado": promocion_estado,
        })

    request.session["carrito"] = carrito_session

    return render(request, "cotizaciones/ver_cotizacion.html", {
        "carrito": carrito,
        "pendientes_cotizaciones": _cotizaciones_pendientes_cliente(cliente),
    })


@require_POST
def eliminar_producto(request):
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


@login_required
def guardar_cotizacion(request):
    carrito = request.session.get('carrito', {})
    if not carrito:
        messages.error(request, _('You must add at least one product before sending the order request.'))
        return redirect('ver_cotizacion')

    nota = request.POST.get("nota", "")

    cliente = _cliente_from_user(request.user)

    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        vendedor=None,
        estado='ENVIADA',
        nota_cliente=nota,
        total=0
    )

    total = Decimal('0')

    # First pass: resolve fresh prices and product ids for every line so that
    # combo promotions can sum quantities across all products before saving.
    presentaciones_cache = {}
    for key, item in list(carrito.items()):
        presentacion = Presentacion.objects.get(id=item["presentacion_id"])
        presentaciones_cache[key] = presentacion
        precio = _quote_item_price_for_customer(
            cliente=cliente,
            presentacion=presentacion,
            session_price=item.get("precio", 0),
        )
        item["producto_id"] = presentacion.producto_id
        item["presentacion_id"] = presentacion.id
        item["precio"] = float(precio)

    reaplicar_promociones_en_lineas_sesion(carrito, cliente=cliente)

    for key, item in carrito.items():

        presentacion = presentaciones_cache[key]
        cantidad = item["cantidad"]
        precio = _quote_item_price_for_customer(
            cliente=cliente,
            presentacion=presentacion,
            session_price=item.get("precio", 0),
        )
        descuento_aplicado = bool(item.get("descuento_aplicado"))
        descuento_monto = _parse_decimal(item.get("descuento_monto", 0), 0) if descuento_aplicado else Decimal('0.00')
        subtotal = calcular_subtotal_item_pedido(
            precio=precio,
            cantidad=cantidad,
            descuento_aplicado=descuento_aplicado,
            descuento_monto=descuento_monto,
        )

        CotizacionItem.objects.create(
            cotizacion=cotizacion,
            presentacion=presentacion,
            cantidad=cantidad,
            precio=precio,
            subtotal=subtotal,
            descuento_aplicado=descuento_aplicado,
            descuento_monto=descuento_monto,
        )

        total += subtotal

    cotizacion.total = total
    cotizacion.save(update_fields=['total'])
    # Belt-and-suspenders: re-apply any qualifying promos that session math missed
    # (e.g. $0 line price with a fixed-dollar or list-price-based % promo).
    asegurar_promociones_en_cotizacion(cotizacion)

    items = cotizacion.items.all()

    crear_notificacion_backoffice(
        titulo=_('New order request #%(id)s') % {'id': cotizacion.id},
        mensaje=_('%(client)s submitted a new order request.') % {'client': cliente.nombre_empresa},
        tipo='PEDIDO',
        url=reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]),
    )

    backoffice_email_ok = True
    cliente_email_sent = False
    tiene_email = _cliente_tiene_email_registrado(cliente)

    try:
        notificar_backoffice_solicitud_cotizacion(
            cotizacion=cotizacion,
            cliente=cliente,
            items=items,
            nota=nota,
        )
    except Exception as exc:
        logger.exception("Error enviando correo de cotización %s: %s", cotizacion.id, exc)
        backoffice_email_ok = False

    if tiene_email:
        try:
            cliente_email_sent = notificar_cliente_solicitud_cotizacion(
                cotizacion,
                include_prices=False,
            )
        except Exception as exc:
            logger.exception(
                "Error enviando copia de solicitud al cliente para cotizacion %s: %s",
                cotizacion.id,
                exc,
            )
            cliente_email_sent = False

    request.session['carrito'] = {}

    if request.POST.get('ajax') == '1':
        if cliente_email_sent:
            email_notice = _(
                'A copy of your order was sent to your registered email address without prices.'
            )
        elif not tiene_email:
            email_notice = _(
                'Your order request was received, but no confirmation email was sent because there is no email address on file for your account.'
            )
        else:
            email_notice = _(
                'Your order request was saved, but the confirmation email could not be sent.'
            )
        return JsonResponse({
            'success': True,
            'cotizacion_id': cotizacion.id,
            'cliente_email_sent': cliente_email_sent,
            'cliente_tiene_email': tiene_email,
            'backoffice_email_ok': backoffice_email_ok,
            'email_notice': email_notice,
            'message': _('Your order request was sent successfully.'),
        })

    if backoffice_email_ok:
        messages.success(request, _('Your order request was sent successfully.'), extra_tags='client-only')
    else:
        messages.warning(
            request,
            _('The order request was saved, but the notification email could not be sent.'),
        )

    if cliente_email_sent:
        messages.info(
            request,
            _('A copy of your order was sent to your registered email address without prices.'),
            extra_tags='client-only',
        )
    elif not tiene_email:
        messages.warning(
            request,
            _(
                'Your order request was received, but no confirmation email was sent because there is no email address on file for your account.'
            ),
            extra_tags='client-only',
        )
    else:
        messages.warning(
            request,
            _('Your order request was saved, but the confirmation email could not be sent.'),
            extra_tags='client-only',
        )

    return redirect('catalogo')


@login_required
@internal_permission_required('backoffice.quotes.view', 'vendor.quotes.view')
def backoffice_cotizaciones(request):
    search_query = (request.GET.get('q') or '').strip()
    view_mode = (request.GET.get('view') or 'active').strip()
    if view_mode not in {'active', 'confirmed', 'cancelled', 'processed', 'all'}:
        view_mode = 'active'

    queryset = Cotizacion.objects.select_related(
        'cliente',
        'vendedor',
        'pedido_generado',
    ).order_by('-fecha', '-id')

    if not user_has_permission(request.user, 'backoffice.quotes.view'):
        assigned_ids = filter_clientes_for_vendedor(Cliente.objects.all(), request.user).values_list('id', flat=True)
        queryset = queryset.filter(cliente_id__in=assigned_ids)

    pending_statuses = {'BORRADOR', 'ENVIADA', 'LISTA_PARA_CONFIRMACION'}
    confirmed_statuses = {'CONFIRMADA_CLIENTE'}
    cancelled_statuses = {'CANCELADA_CLIENTE', 'RECHAZADA'}
    processed_statuses = {'APROBADA'}

    counts_base = queryset
    pending_count = counts_base.filter(estado__in=pending_statuses).count()
    confirmed_count = counts_base.filter(estado__in=confirmed_statuses).count()
    cancelled_count = counts_base.filter(estado__in=cancelled_statuses).count()
    processed_count = counts_base.filter(
        Q(estado__in=processed_statuses) | Q(pedido_generado__isnull=False)
    ).distinct().count()
    all_count = counts_base.count()

    if view_mode == 'confirmed':
        queryset = queryset.filter(estado__in=confirmed_statuses)
    elif view_mode == 'cancelled':
        queryset = queryset.filter(estado__in=cancelled_statuses)
    elif view_mode == 'processed':
        queryset = queryset.filter(Q(estado__in=processed_statuses) | Q(pedido_generado__isnull=False)).distinct()
    elif view_mode == 'all':
        pass
    else:
        view_mode = 'active'
        queryset = queryset.filter(estado__in=pending_statuses)

    if search_query:
        search_filter = (
            Q(cliente__nombre_empresa__icontains=search_query)
            | Q(cliente__usuario__username__icontains=search_query)
            | Q(estado__icontains=search_query)
            | Q(nota_cliente__icontains=search_query)
        )
        if search_query.isdigit():
            search_filter |= Q(id=int(search_query))
        queryset = queryset.filter(search_filter)

    page_obj = Paginator(queryset, BACKOFFICE_QUOTES_PAGE_SIZE).get_page(request.GET.get('page'))
    for cotizacion in page_obj:
        cotizacion.pedido_existente = _get_generated_order_from_quote(cotizacion)
        cotizacion.can_generate_order = (
            cotizacion.pedido_existente is None
            and cotizacion.backoffice_pricing_confirmed
            and cotizacion.estado in {'CONFIRMADA_CLIENTE', 'LISTA_PARA_CONFIRMACION', 'APROBADA'}
            and user_has_permission(request.user, 'backoffice.orders.manage')
        )

    return render(request, 'backoffice/cotizaciones_lista.html', {
        'cotizaciones': page_obj,
        'page_obj': page_obj,
        'view_mode': view_mode,
        'search_query': search_query,
        'pending_count': pending_count,
        'confirmed_count': confirmed_count,
        'cancelled_count': cancelled_count,
        'processed_count': processed_count,
        'all_count': all_count,
        'can_create_quote': (
            user_has_permission(request.user, 'backoffice.quotes.manage')
            or user_has_permission(request.user, 'vendor.quotes.manage')
        ),
        'can_generate_orders': user_has_permission(request.user, 'backoffice.orders.manage'),
    })


@login_required
@internal_permission_required('backoffice.quotes.view', 'vendor.quotes.view')
def backoffice_cotizacion_detalle(request, cotizacion_id):
    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'),
        id=cotizacion_id,
    )
    if not user_can_view_cotizacion(request.user, cotizacion):
        messages.error(request, _('You do not have permission to access this section.'))
        return redirect('vendedor_home' if getattr(request.user, 'role', '') == 'vendedor' else 'backoffice_cotizaciones')

    pedido_existente = _get_generated_order_from_quote(cotizacion)
    can_manage = user_can_manage_cotizacion(request.user, cotizacion)

    if request.method == 'POST':
        if not can_manage:
            messages.error(request, _('You do not have permission to update this quote.'))
            return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

        if pedido_existente is not None:
            messages.error(
                request,
                _('This quote cannot be edited because a sales order was already generated from it.'),
            )
            return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

        quote_items = list(cotizacion.items.select_related('presentacion__producto'))
        deleted_ids = {
            item.id for item in quote_items if request.POST.get(f'eliminar_{item.id}')
        }
        nueva_presentacion_id = (request.POST.get('presentacion_nueva') or '').strip()
        remaining_count = sum(1 for item in quote_items if item.id not in deleted_ids)
        if nueva_presentacion_id:
            remaining_count += 1

        if remaining_count == 0:
            messages.error(request, _('You must leave at least one product in the quote.'))
            return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

        updated_items = []
        validation_error = ''
        for item in quote_items:
            if item.id in deleted_ids:
                continue

            cantidad = _parse_quantity(request.POST.get(f'cantidad_{item.id}'), item.cantidad)
            precio = _parse_decimal(request.POST.get(f'precio_{item.id}'), item.precio)
            validation_error = _validate_backoffice_quote_price(item=item, price=precio)
            if validation_error:
                break

            try:
                descuento_aplicado, descuento_monto = normalizar_descuento_item_pedido(
                    precio=precio,
                    descuento_aplicado=request.POST.get(f'descuento_aplicado_{item.id}'),
                    descuento_monto=_parse_decimal(request.POST.get(f'descuento_monto_{item.id}'), item.descuento_monto),
                )
            except ValidationError as exc:
                validation_error = exc.messages[0] if getattr(exc, 'messages', None) else str(exc)
                break

            updated_items.append((item, cantidad, precio, descuento_aplicado, descuento_monto))

        nueva_presentacion = None
        if not validation_error and nueva_presentacion_id:
            nueva_presentacion = get_object_or_404(
                Presentacion.objects.select_related('producto'),
                id=nueva_presentacion_id,
            )
            precio_nuevo = _parse_decimal(request.POST.get('precio_nuevo'), 0)
            validation_error = _validate_backoffice_quote_price(presentacion=nueva_presentacion, price=precio_nuevo)

        if validation_error:
            messages.error(request, validation_error)
            return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

        with transaction.atomic():
            if deleted_ids:
                CotizacionItem.objects.filter(cotizacion=cotizacion, id__in=deleted_ids).delete()

            total = Decimal('0')
            for item, cantidad, precio, descuento_aplicado, descuento_monto in updated_items:
                item.cantidad = cantidad
                item.precio = precio
                item.descuento_aplicado = descuento_aplicado
                item.descuento_monto = descuento_monto
                item.subtotal = calcular_subtotal_item_pedido(
                    precio=precio,
                    cantidad=cantidad,
                    descuento_aplicado=descuento_aplicado,
                    descuento_monto=descuento_monto,
                )
                item.save(update_fields=['cantidad', 'precio', 'descuento_aplicado', 'descuento_monto', 'subtotal'])
                total += item.subtotal

            if nueva_presentacion is not None:
                cantidad_nueva = _parse_quantity(request.POST.get('cantidad_nueva'), 1)
                precio_nuevo = _parse_decimal(request.POST.get('precio_nuevo'), 0)
                descuento_aplicado_nuevo, descuento_monto_nuevo = normalizar_descuento_item_pedido(
                    precio=precio_nuevo,
                    descuento_aplicado=request.POST.get('descuento_aplicado_nuevo'),
                    descuento_monto=_parse_decimal(request.POST.get('descuento_monto_nuevo'), 0),
                )
                new_item = CotizacionItem.objects.create(
                    cotizacion=cotizacion,
                    presentacion=nueva_presentacion,
                    cantidad=cantidad_nueva,
                    precio=precio_nuevo,
                    descuento_aplicado=descuento_aplicado_nuevo,
                    descuento_monto=descuento_monto_nuevo,
                    subtotal=calcular_subtotal_item_pedido(
                        precio=precio_nuevo,
                        cantidad=cantidad_nueva,
                        descuento_aplicado=descuento_aplicado_nuevo,
                        descuento_monto=descuento_monto_nuevo,
                    ),
                )
                total += new_item.subtotal

            cotizacion.nota_backoffice = (request.POST.get('nota_backoffice') or '').strip()
            cotizacion.total = total
            cotizacion.backoffice_pricing_confirmed = True
            cotizacion.save(update_fields=['nota_backoffice', 'total', 'backoffice_pricing_confirmed'])

        _set_quote_send_ready(request.session, cotizacion.id, True)
        messages.success(request, _('Quote updated successfully.'))
        return redirect(f"{reverse('backoffice_cotizacion_detalle', args=[cotizacion.id])}?saved=1")

    if request.GET.get('saved') == '1':
        _set_quote_send_ready(request.session, cotizacion.id, True)
    else:
        _set_quote_send_ready(request.session, cotizacion.id, False)

    # Auto-apply configured Promotions onto lines that still lack a discount so
    # BackOffice opens with Apply discount already checked.
    if pedido_existente is None:
        asegurar_promociones_en_cotizacion(cotizacion)

    confirm_url, telefono_contacto, whatsapp_link, outbound_message = _get_whatsapp_contact_data(cotizacion, request)
    cotizacion_item_rows, display_total, order_profit_summary = _build_quote_item_rows(cotizacion)
    can_send_customer_quote = _is_quote_send_ready(request.session, cotizacion.id) and can_manage
    can_generate_backoffice_order = bool(
        user_has_permission(request.user, 'backoffice.orders.manage')
        and cotizacion.backoffice_pricing_confirmed
        and cotizacion.items.exists()
        and pedido_existente is None
    )

    context = {
        'cotizacion': cotizacion,
        'pedido_existente': pedido_existente,
        'can_manage_quote_lines': pedido_existente is None and can_manage,
        'cotizacion_item_rows': cotizacion_item_rows,
        'bulk_price_options': _build_bulk_quote_price_options(),
        'discount_preset_options': _build_quote_discount_preset_options(),
        'display_total': display_total,
        'order_profit_summary': order_profit_summary,
        'can_send_customer_quote': can_send_customer_quote,
        'can_generate_backoffice_order': can_generate_backoffice_order,
        'can_void_cotizacion': (
            user_has_permission(request.user, 'backoffice.quotes.manage')
            and _puede_anular_cotizacion_desde_backoffice(cotizacion)
        ),
        'can_delete_cotizacion': (
            user_has_permission(request.user, 'backoffice.quotes.manage')
            and _puede_eliminar_cotizacion_desde_backoffice(cotizacion)
        ),
        'confirm_url': confirm_url,
        'telefono_contacto': telefono_contacto,
        'whatsapp_link': whatsapp_link,
        'outbound_message': outbound_message,
        'cliente_tiene_email': _cliente_tiene_email(cotizacion.cliente),
    }
    return render(request, 'backoffice/cotizacion_detalle.html', context)


@login_required
@require_POST
@internal_permission_required('backoffice.quotes.manage')
def backoffice_cotizacion_void(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related('cliente__usuario'), id=cotizacion_id)
    try:
        _anular_cotizacion_desde_backoffice(cotizacion=cotizacion)
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    messages.success(request, _('Quote voided successfully. Inventory was not changed.'))
    return redirect('backoffice_cotizaciones')


@login_required
@require_POST
@internal_permission_required('backoffice.quotes.manage')
def backoffice_cotizacion_delete(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related('cliente__usuario'), id=cotizacion_id)
    try:
        _eliminar_cotizacion_desde_backoffice(cotizacion=cotizacion)
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    messages.success(request, _('Quote deleted permanently. Inventory was not changed.'))
    return redirect('backoffice_cotizaciones')


@login_required
@require_POST
@internal_permission_required('backoffice.quotes.manage', 'vendor.quotes.manage')
def enviar_cotizacion_cliente(request, cotizacion_id):
    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'),
        id=cotizacion_id,
    )
    if not user_can_manage_cotizacion(request.user, cotizacion):
        messages.error(request, _('You do not have permission to access this section.'))
        return redirect('vendedor_home' if getattr(request.user, 'role', '') == 'vendedor' else 'backoffice_cotizaciones')

    if not cotizacion.items.exists():
        messages.error(request, _('The order has no products to send to the customer.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    if not _is_quote_send_ready(request.session, cotizacion.id):
        messages.warning(request, _('Save the order changes before sending it to the customer.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    confirm_url, telefono_contacto, whatsapp_link, outbound_message = _get_whatsapp_contact_data(cotizacion, request)
    include_prices_in_email = _email_includes_prices(request)
    cliente_tiene_email = _cliente_tiene_email(cotizacion.cliente)
    now = timezone.now()
    updates = ['estado']
    cotizacion.estado = 'LISTA_PARA_CONFIRMACION'

    if cliente_tiene_email:
        try:
            from config.core.email_branding import brand_email_context

            html_content = render_to_string(
                'emails/cotizacion_lista_cliente.html',
                {
                    'cliente': cotizacion.cliente,
                    'cotizacion': cotizacion,
                    'confirm_url': confirm_url,
                    'include_prices': include_prices_in_email,
                    'items': cotizacion.items.select_related('presentacion__producto'),
                    **brand_email_context(),
                },
            )

            email = EmailMultiAlternatives(
                subject=_('Order ready to confirm #{id}').format(id=cotizacion.id),
                body=_('Your order is ready to confirm: {url}').format(url=confirm_url),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[cotizacion.cliente.usuario.email.strip()],
            )
            email.attach_alternative(html_content, 'text/html')
            email.send(fail_silently=False)
            cotizacion.correo_enviado = True
            cotizacion.correo_enviado_en = now
            updates.extend(['correo_enviado', 'correo_enviado_en'])
        except Exception as exc:
            logger.exception('Error enviando correo al cliente para cotizacion %s: %s', cotizacion.id, exc)

    sms_sent = False
    whatsapp_sent = False

    try:
        sms_sent = _send_twilio_message(phone_number=telefono_contacto, message=outbound_message, channel='sms')
    except Exception as exc:
        logger.exception('Error enviando SMS de cotizacion %s: %s', cotizacion.id, exc)

    try:
        whatsapp_sent = _send_twilio_message(phone_number=telefono_contacto, message=outbound_message, channel='whatsapp')
    except Exception as exc:
        logger.exception('Error enviando WhatsApp automatico de cotizacion %s: %s', cotizacion.id, exc)

    if sms_sent:
        cotizacion.sms_enviado = True
        cotizacion.sms_enviado_en = now
        updates.extend(['sms_enviado', 'sms_enviado_en'])

    if whatsapp_sent:
        cotizacion.whatsapp_enviado = True
        cotizacion.whatsapp_enviado_en = now
        updates.extend(['whatsapp_enviado', 'whatsapp_enviado_en'])

    cotizacion.save(update_fields=list(dict.fromkeys(updates)))

    if cotizacion.correo_enviado:
        success_message = _('The order was sent to the customer by email.')
        if sms_sent or whatsapp_sent:
            success_message += ' ' + _('Additional automatic channels were processed.')
        elif telefono_contacto and whatsapp_link:
            success_message += ' ' + _('The manual WhatsApp link is available as a fallback.')
        messages.success(request, success_message)
    elif not cliente_tiene_email:
        warning_message = _(
            'The order was marked as ready to confirm, but this customer does not have an email on file.'
        )
        if sms_sent or whatsapp_sent:
            warning_message += ' ' + _('Additional automatic channels were processed.')
        elif telefono_contacto and whatsapp_link:
            warning_message += ' ' + _('The manual WhatsApp link is available as a fallback.')
        messages.warning(request, warning_message)
    else:
        messages.warning(request, _('The order was marked as ready to confirm, but the email could not be sent.'))

    _set_quote_send_ready(request.session, cotizacion.id, False)
    return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)


@login_required
@internal_permission_required('backoffice.quotes.manage', 'vendor.quotes.manage')
def abrir_whatsapp_manual_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related('cliente__usuario'), id=cotizacion_id)
    if not user_can_manage_cotizacion(request.user, cotizacion):
        messages.error(request, _('You do not have permission to access this section.'))
        return redirect('vendedor_home' if getattr(request.user, 'role', '') == 'vendedor' else 'backoffice_cotizaciones')
    confirm_url, phone_number, whatsapp_link, outbound_message = _get_whatsapp_contact_data(cotizacion, request)

    if not _is_quote_send_ready(request.session, cotizacion.id):
        messages.warning(request, _('Save the order changes before opening WhatsApp for the customer.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    if not whatsapp_link:
        messages.warning(request, _('No valid phone number available to open WhatsApp manually.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    cotizacion.whatsapp_manual_abierto = True
    cotizacion.whatsapp_manual_abierto_en = timezone.now()
    cotizacion.save(update_fields=['whatsapp_manual_abierto', 'whatsapp_manual_abierto_en'])
    _set_quote_send_ready(request.session, cotizacion.id, False)
    return HttpResponseRedirect(whatsapp_link)


@login_required
@require_POST
@internal_permission_required('backoffice.orders.manage')
def generar_pedido_desde_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'),
        id=cotizacion_id,
    )

    pedido_existente = _get_generated_order_from_quote(cotizacion)
    if pedido_existente is not None:
        messages.info(
            request,
            _('This quote already has sales order #%(id)s generated.') % {'id': pedido_existente.id},
        )
        return redirect('backoffice_pedido_detalle', pedido_id=pedido_existente.id)

    if not cotizacion.items.exists():
        messages.error(request, _('The order has no products to generate a sales order.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    if not cotizacion.backoffice_pricing_confirmed:
        messages.warning(request, _('Save and confirm the quote pricing before generating the sales order.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    items_payload = _build_order_items_payload_from_quote(cotizacion)
    if not items_payload:
        messages.error(request, _('You must leave at least one product to create the sales order.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    nota_cliente = (cotizacion.nota_confirmacion_cliente or '').strip()
    if not nota_cliente:
        nota_cliente = _('Order confirmed with the customer by BackOffice.')

    with transaction.atomic():
        pedido = _create_purchase_order_from_quote(
            cotizacion=cotizacion,
            items_payload=items_payload,
            nota_cliente=nota_cliente,
            origen='CLIENTE',
            canal_toma='backoffice',
            acepta_terminos=True,
        )

    try:
        notificar_backoffice_pedido(pedido)
    except Exception as exc:
        logger.exception('Error notificando pedido generado desde BackOffice %s: %s', pedido.id, exc)

    cliente_notificado = False
    include_prices_in_email = _email_includes_prices(request)
    cliente_tiene_email = _cliente_tiene_email(pedido.cliente)
    try:
        cliente_notificado = notificar_cliente_pedido(pedido, include_prices=include_prices_in_email)
    except Exception as exc:
        logger.exception('Error notificando al cliente sobre el pedido %s generado desde BackOffice: %s', pedido.id, exc)

    _set_quote_send_ready(request.session, cotizacion.id, False)

    if cliente_notificado:
        messages.success(
            request,
            _('Sales order #%(id)s was generated successfully and the customer was notified.') % {'id': pedido.id},
        )
    elif not cliente_tiene_email:
        messages.warning(
            request,
            _('Sales order #%(id)s was generated successfully, but this customer does not have an email on file.') % {
                'id': pedido.id,
            },
        )
    else:
        messages.warning(
            request,
            _('Sales order #%(id)s was generated, but the customer email could not be sent.') % {'id': pedido.id},
        )

    return redirect('backoffice_pedido_detalle', pedido_id=pedido.id)


def cliente_cotizaciones_recibidas(request):
    if not request.user.is_authenticated:
        return _redirect_to_home_login(request)

    if getattr(request.user, 'role', '') != 'cliente':
        logout(request)
        return _redirect_to_home_login(request)

    cliente = _cliente_from_user(request.user)
    base_queryset = Cotizacion.objects.filter(
        cliente=cliente,
        estado__in=['LISTA_PARA_CONFIRMACION', 'CONFIRMADA_CLIENTE', 'CANCELADA_CLIENTE'],
    ).prefetch_related('items').order_by('-fecha')
    view_mode = request.GET.get('view')

    if view_mode == 'confirmed':
        cotizaciones = base_queryset.filter(estado='CONFIRMADA_CLIENTE')
    elif view_mode == 'cancelled':
        cotizaciones = base_queryset.filter(estado='CANCELADA_CLIENTE')
    else:
        view_mode = 'pending'
        cotizaciones = base_queryset.filter(estado='LISTA_PARA_CONFIRMACION')

    context = {
        'cotizaciones': cotizaciones,
        'pendientes_cotizaciones': _cotizaciones_pendientes_cliente(cliente),
        'pendientes_count': base_queryset.filter(estado='LISTA_PARA_CONFIRMACION').count(),
        'confirmed_count': base_queryset.filter(estado='CONFIRMADA_CLIENTE').count(),
        'cancelled_count': base_queryset.filter(estado='CANCELADA_CLIENTE').count(),
        'view_mode': view_mode,
    }
    return render(request, 'cotizaciones/cliente_cotizaciones_recibidas.html', context)


def cliente_historial_ordenes(request):
    if not request.user.is_authenticated:
        return _redirect_to_home_login(request)

    if getattr(request.user, 'role', '') != 'cliente':
        logout(request)
        return _redirect_to_home_login(request)

    cliente = _cliente_from_user(request.user)
    pedidos = list(list_cliente_purchase_orders(cliente=cliente)[:100])
    for pedido in pedidos:
        pedido.estado_label = _cliente_pedido_estado_label(pedido.estado)
        pedido.product_total_display = int(pedido.product_unit_count or 0)

    context = {
        'pedidos': pedidos,
        'pendientes_cotizaciones': _cotizaciones_pendientes_cliente(cliente),
    }
    return render(request, 'cotizaciones/cliente_historial_ordenes.html', context)


@require_POST
def cliente_reordenar_pedido(request, pedido_id):
    if not request.user.is_authenticated:
        return _redirect_to_home_login(request)

    if getattr(request.user, 'role', '') != 'cliente':
        logout(request)
        return _redirect_to_home_login(request)

    cliente = _cliente_from_user(request.user)
    pedido = get_object_or_404(
        Pedido.objects.prefetch_related('items__presentacion__producto'),
        id=pedido_id,
        cliente=cliente,
    )

    def price_fn(*, presentacion):
        return _quote_item_price_for_customer(
            cliente=cliente,
            presentacion=presentacion,
            session_price=0,
        )

    carrito, added_count = merge_pedido_into_session_cart(
        carrito=request.session.get('carrito', {}) or {},
        pedido=pedido,
        price_fn=price_fn,
        promo_fn=functools.partial(aplicar_promocion_en_item_sesion, cliente=cliente),
    )
    request.session['carrito'] = carrito
    request.session.modified = True

    if added_count:
        messages.success(
            request,
            _('Order #%(number)s was added to your cart. Review quantities before submitting.') % {
                'number': pedido.numero_display,
            },
        )
    else:
        messages.warning(
            request,
            _('No active products from order #%(number)s could be added to your cart.') % {
                'number': pedido.numero_display,
            },
        )
    return redirect('ver_cotizacion')


def cliente_cotizacion_recibida_detalle(request, token):
    if not request.user.is_authenticated:
        return _redirect_to_home_login(request)

    if getattr(request.user, 'role', '') != 'cliente':
        logout(request)
        return _redirect_to_home_login(request)

    cliente = _cliente_from_user(request.user)
    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'),
        token_cliente=token,
        cliente=cliente,
    )

    pedido_existente = _get_generated_order_from_quote(cotizacion)

    puede_editar = cotizacion.estado == 'LISTA_PARA_CONFIRMACION' and pedido_existente is None

    def build_cliente_quote_rows(post_data=None):
        rows = []
        items_payload = []
        total = Decimal('0.00')

        for item in list(cotizacion.items.select_related('presentacion__producto')):
            marked_delete = bool(post_data and post_data.get(f'eliminar_{item.id}'))
            quantity = _parse_quantity(
                post_data.get(f'cantidad_{item.id}') if post_data else item.cantidad,
                item.cantidad,
            )
            list_price = _parse_decimal(item.precio)
            discount_applied = bool(item.descuento_aplicado)
            discount_amount = _parse_decimal(item.descuento_monto, 0) if discount_applied else Decimal('0.00')
            net_unit_price = calcular_precio_unitario_neto_item(
                precio=list_price,
                descuento_aplicado=discount_applied,
                descuento_monto=discount_amount,
            )
            subtotal = calcular_subtotal_item_pedido(
                precio=list_price,
                cantidad=quantity,
                descuento_aplicado=discount_applied,
                descuento_monto=discount_amount,
            )
            discount_line_total = (
                (discount_amount * Decimal(str(quantity or 0))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if discount_applied
                else Decimal('0.00')
            )

            rows.append({
                'item': item,
                'quantity': quantity,
                'list_price': list_price,
                'descuento_aplicado': discount_applied,
                'descuento_monto': discount_amount,
                'descuento_linea_total': discount_line_total,
                'precio_unitario_neto': net_unit_price,
                'subtotal': subtotal,
                'marked_delete': marked_delete,
            })

            if marked_delete:
                continue

            total += subtotal
            items_payload.append({
                'presentacion': item.presentacion,
                'cantidad': quantity,
                'precio': list_price,
                'descuento_aplicado': discount_applied,
                'descuento_monto': discount_amount,
            })

        return rows, items_payload, total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    if request.method == 'POST':
        if not puede_editar:
            messages.info(request, _('This order can no longer be edited.'))
            return redirect('cliente_cotizacion_recibida_detalle', token=cotizacion.token_cliente)

        accion = (request.POST.get('accion') or '').strip()
        nota_cliente = (request.POST.get('nota_cliente') or '').strip()
        quote_rows, items_payload, total = build_cliente_quote_rows(request.POST)

        if accion == 'cancelar':
            with transaction.atomic():
                for row in quote_rows:
                    item = row['item']
                    if row['marked_delete']:
                        item.delete()
                        continue

                    item.cantidad = row['quantity']
                    item.subtotal = row['subtotal']
                    item.save(update_fields=['cantidad', 'subtotal'])

                cotizacion.total = total
                cotizacion.nota_confirmacion_cliente = nota_cliente
                cotizacion.estado = 'CANCELADA_CLIENTE'
                cotizacion.save(update_fields=['estado', 'total', 'nota_confirmacion_cliente'])
                crear_notificacion_backoffice(
                    titulo=_('Order request cancelled #%(id)s') % {'id': cotizacion.id},
                    mensaje=_('%(client)s cancelled the order request.') % {'client': cotizacion.cliente.nombre_empresa},
                    tipo='PEDIDO',
                    url=reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]),
                )
                messages.warning(request, _('The quote was cancelled successfully.'))
                return redirect('cliente_cotizacion_recibida_detalle', token=cotizacion.token_cliente)

        if not items_payload:
            messages.error(request, _('You must leave at least one product to create the sales order.'))
            context = {
                'cotizacion': cotizacion,
                'pedido_existente': pedido_existente,
                'puede_editar': puede_editar,
                'pendientes_cotizaciones': _cotizaciones_pendientes_cliente(cliente),
                'quote_rows': quote_rows,
                'current_total': total,
                'pending_nota_cliente': nota_cliente,
                'pending_acepta_terminos': bool(request.POST.get('acepta_terminos')),
            }
            return render(request, 'cotizaciones/cliente_confirmar_cotizacion.html', context)

        if not request.POST.get('acepta_terminos'):
            messages.error(request, _('You must accept the terms and prices to continue with the order.'))
            context = {
                'cotizacion': cotizacion,
                'pedido_existente': pedido_existente,
                'puede_editar': puede_editar,
                'pendientes_cotizaciones': _cotizaciones_pendientes_cliente(cliente),
                'quote_rows': quote_rows,
                'current_total': total,
                'pending_nota_cliente': nota_cliente,
                'pending_acepta_terminos': False,
            }
            return render(request, 'cotizaciones/cliente_confirmar_cotizacion.html', context)

        with transaction.atomic():
            for row in quote_rows:
                item = row['item']
                if row['marked_delete']:
                    item.delete()
                    continue

                item.cantidad = row['quantity']
                item.subtotal = row['subtotal']
                item.save(update_fields=['cantidad', 'subtotal'])

            cotizacion.total = total
            cotizacion.nota_confirmacion_cliente = nota_cliente
            pedido = _create_purchase_order_from_quote(
                cotizacion=cotizacion,
                items_payload=items_payload,
                nota_cliente=nota_cliente,
                origen='CLIENTE',
                canal_toma='portal',
                acepta_terminos=True,
            )

        try:
            notificar_backoffice_pedido(pedido)
        except Exception as exc:
            logger.exception('Error notificando pedido confirmado %s a BackOffice: %s', pedido.id, exc)

        try:
            notificar_cliente_pedido(pedido)
        except Exception as exc:
            logger.exception('Error notificando pedido confirmado %s al cliente: %s', pedido.id, exc)

        messages.success(request, _('Your sales order #{id} was sent successfully.').format(id=pedido.id))
        return redirect('cliente_cotizacion_recibida_detalle', token=cotizacion.token_cliente)

    quote_rows, current_items_payload, current_total = build_cliente_quote_rows()

    context = {
        'cotizacion': cotizacion,
        'pedido_existente': pedido_existente,
        'puede_editar': puede_editar,
        'pendientes_cotizaciones': _cotizaciones_pendientes_cliente(cliente),
        'quote_rows': quote_rows,
        'pending_nota_cliente': cotizacion.nota_confirmacion_cliente or '',
        'pending_acepta_terminos': False,
        'current_items_payload': current_items_payload,
        'current_total': current_total,
    }
    return render(request, 'cotizaciones/cliente_confirmar_cotizacion.html', context)



