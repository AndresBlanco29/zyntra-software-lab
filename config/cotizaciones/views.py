import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.clientes.models import Cliente
from config.notificaciones.models import crear_notificacion_backoffice
from config.pedidos.models import Pedido
from config.facturacion.services import get_recent_customer_invoice_items_by_presentation
from config.pedidos.services import (
    crear_pedido_desde_items,
    notificar_backoffice_pedido,
    notificar_cliente_pedido,
)
from config.productos.models import ConfiguracionPrecios, Presentacion
from config.usuarios.permissions import internal_permission_required

from .models import Cotizacion, CotizacionItem
from django.utils.translation import gettext as _


logger = logging.getLogger(__name__)

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


def _calculate_quote_utility_percentage(cost, price):
    if cost is None:
        return None

    cost_decimal = _parse_decimal(cost, 0)
    price_decimal = _parse_decimal(price, 0)
    if price_decimal <= 0:
        return None

    percentage = (Decimal('1') - (cost_decimal / price_decimal)) * Decimal('100')
    return percentage.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


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


def _validate_backoffice_quote_price(*, item, price):
    product_label = f'{item.presentacion.producto.nombre} ({item.presentacion.nombre})'
    cost = item.presentacion.costo

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


def _build_quote_item_rows(cotizacion):
    margin_values = ConfiguracionPrecios.obtener().porcentajes_lista()
    rows = []
    display_total = Decimal('0.00')
    quote_items = list(cotizacion.items.select_related('presentacion__producto'))
    recent_orders_by_presentation = get_recent_customer_invoice_items_by_presentation(
        cliente=cotizacion.cliente,
        presentation_ids=[item.presentacion_id for item in quote_items],
    )

    for item in quote_items:
        current_price = _default_backoffice_quote_price(item, cotizacion)
        display_subtotal = (current_price * Decimal(str(item.cantidad))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
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

        rows.append({
            'item': item,
            'price_options': price_options,
            'selected_option': selected_option,
            'current_price': current_price,
            'display_subtotal': display_subtotal,
            'cost': item.presentacion.costo,
            'minimum_price': max(
                (_parse_decimal(item.presentacion.costo, 0) if item.presentacion.costo is not None else Decimal('0.00')),
                Decimal('1.01'),
            ),
            'current_utility_percentage': _calculate_quote_utility_percentage(item.presentacion.costo, current_price),
            'recent_customer_sales': recent_orders_by_presentation.get(item.presentacion_id, []),
        })
        display_total += display_subtotal

    return rows, display_total


def _build_bulk_quote_price_options():
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


def _get_generated_order_from_quote(cotizacion):
    try:
        return cotizacion.pedido_generado
    except Pedido.DoesNotExist:
        return None


def _build_order_items_payload_from_quote(cotizacion):
    items_payload = []
    for item in cotizacion.items.select_related('presentacion__producto'):
        items_payload.append({
            'presentacion': item.presentacion,
            'cantidad': item.cantidad,
            'precio': item.precio,
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


@login_required
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
        "carrito": carrito,
        "pendientes_cotizaciones": _cotizaciones_pendientes_cliente(_cliente_from_user(request.user)),
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

    for item in carrito.values():

        presentacion = Presentacion.objects.get(id=item["presentacion_id"])
        cantidad = item["cantidad"]
        precio = _quote_item_price_for_customer(
            cliente=cliente,
            presentacion=presentacion,
            session_price=item.get("precio", 0),
        )
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
    cotizacion.save(update_fields=['total'])

    items = cotizacion.items.all()

    crear_notificacion_backoffice(
        titulo=f'New order request #{cotizacion.id}',
        mensaje=f'{cliente.nombre_empresa} requested a new order.',
        tipo='COTIZACION',
        url=f'/cotizaciones/backoffice/{cotizacion.id}/',
    )

    html_content = render_to_string(
        "emails/cotizacion_cliente.html",
        {
            "cliente": cliente,
            "items": items,
            "nota": nota
        }
    )

    email = EmailMultiAlternatives(
        subject=f"New order request #{cotizacion.id}",
        body=_("A new order request has been received."),
        from_email=settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER,
        to=[settings.ORDERS_NOTIFICATION_EMAIL]
    )

    try:
        email.attach_alternative(html_content, "text/html")
        email.send(fail_silently=False)
        messages.success(request, _('Your order request was sent successfully.'), extra_tags='client-only')
    except Exception as exc:
        logger.exception("Error enviando correo de cotización %s: %s", cotizacion.id, exc)
        messages.warning(
            request,
            _('The order request was saved, but the notification email could not be sent.')
        )

    request.session['carrito'] = {}

    return redirect('catalogo')


@login_required
@internal_permission_required('backoffice.quotes.view')
def backoffice_cotizaciones(request):
    base_queryset = Cotizacion.objects.select_related('cliente__usuario').prefetch_related('items').order_by('-fecha')
    pending_statuses = ['ENVIADA', 'LISTA_PARA_CONFIRMACION']
    processed_statuses = ['APROBADA', 'RECHAZADA', 'BORRADOR']
    view_mode = request.GET.get('view')

    if view_mode == 'confirmed':
        cotizaciones = base_queryset.filter(estado='CONFIRMADA_CLIENTE')
    elif view_mode == 'cancelled':
        cotizaciones = base_queryset.filter(estado='CANCELADA_CLIENTE')
    elif view_mode == 'processed':
        cotizaciones = base_queryset.filter(estado__in=processed_statuses)
    else:
        view_mode = 'pending'
        cotizaciones = base_queryset.filter(estado__in=pending_statuses)

    return render(request, 'backoffice/cotizaciones_lista.html', {
        'cotizaciones': cotizaciones,
        'view_mode': view_mode,
        'pending_count': base_queryset.filter(estado__in=pending_statuses).count(),
        'confirmed_count': base_queryset.filter(estado='CONFIRMADA_CLIENTE').count(),
        'cancelled_count': base_queryset.filter(estado='CANCELADA_CLIENTE').count(),
        'processed_count': base_queryset.filter(estado__in=processed_statuses).count(),
    })


@login_required
@internal_permission_required('backoffice.quotes.view')
def backoffice_cotizacion_detalle(request, cotizacion_id):
    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'),
        id=cotizacion_id,
    )
    pedido_existente = _get_generated_order_from_quote(cotizacion)

    if request.method == 'POST':
        if not request.user.has_internal_permission('backoffice.quotes.manage'):
            messages.error(request, _('You do not have permission to update this quote.'))
            return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

        updated_items = []
        validation_error = ''
        for item in cotizacion.items.select_related('presentacion__producto'):
            cantidad = _parse_quantity(request.POST.get(f'cantidad_{item.id}'), item.cantidad)
            precio = _parse_decimal(request.POST.get(f'precio_{item.id}'), item.precio)
            validation_error = _validate_backoffice_quote_price(item=item, price=precio)
            if validation_error:
                break
            updated_items.append((item, cantidad, precio))

        if validation_error:
            messages.error(request, validation_error)
            return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

        with transaction.atomic():
            total = Decimal('0')
            for item, cantidad, precio in updated_items:
                item.cantidad = cantidad
                item.precio = precio
                item.subtotal = precio * cantidad
                item.save(update_fields=['cantidad', 'precio', 'subtotal'])
                total += item.subtotal

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

    confirm_url, telefono_contacto, whatsapp_link, outbound_message = _get_whatsapp_contact_data(cotizacion, request)
    cotizacion_item_rows, display_total = _build_quote_item_rows(cotizacion)
    can_send_customer_quote = _is_quote_send_ready(request.session, cotizacion.id)
    can_generate_backoffice_order = bool(
        cotizacion.backoffice_pricing_confirmed and cotizacion.items.exists() and pedido_existente is None
    )

    context = {
        'cotizacion': cotizacion,
        'pedido_existente': pedido_existente,
        'cotizacion_item_rows': cotizacion_item_rows,
        'bulk_price_options': _build_bulk_quote_price_options(),
        'display_total': display_total,
        'can_send_customer_quote': can_send_customer_quote,
        'can_generate_backoffice_order': can_generate_backoffice_order,
        'confirm_url': confirm_url,
        'telefono_contacto': telefono_contacto,
        'whatsapp_link': whatsapp_link,
        'outbound_message': outbound_message,
    }
    return render(request, 'backoffice/cotizacion_detalle.html', context)


@login_required
@require_POST
@internal_permission_required('backoffice.quotes.manage')
def enviar_cotizacion_cliente(request, cotizacion_id):
    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related('cliente__usuario').prefetch_related('items__presentacion__producto'),
        id=cotizacion_id,
    )

    if not cotizacion.items.exists():
        messages.error(request, _('The order has no products to send to the customer.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    if not _is_quote_send_ready(request.session, cotizacion.id):
        messages.warning(request, _('Save the order changes before sending it to the customer.'))
        return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)

    confirm_url, telefono_contacto, whatsapp_link, outbound_message = _get_whatsapp_contact_data(cotizacion, request)
    now = timezone.now()
    updates = ['estado']
    cotizacion.estado = 'LISTA_PARA_CONFIRMACION'

    try:
        html_content = render_to_string(
            'emails/cotizacion_lista_cliente.html',
            {
                'cliente': cotizacion.cliente,
                'cotizacion': cotizacion,
                'confirm_url': confirm_url,
            },
        )

        email = EmailMultiAlternatives(
            subject=_('Order ready to confirm #{id}').format(id=cotizacion.id),
            body=_('Your order is ready to confirm: {url}').format(url=confirm_url),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[cotizacion.cliente.usuario.email],
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
    else:
        messages.warning(request, _('The order was marked as ready to confirm, but the email could not be sent.'))

    _set_quote_send_ready(request.session, cotizacion.id, False)
    return redirect('backoffice_cotizacion_detalle', cotizacion_id=cotizacion.id)


@login_required
@internal_permission_required('backoffice.quotes.manage')
def abrir_whatsapp_manual_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion.objects.select_related('cliente__usuario'), id=cotizacion_id)
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
    try:
        cliente_notificado = notificar_cliente_pedido(pedido)
    except Exception as exc:
        logger.exception('Error notificando al cliente sobre el pedido %s generado desde BackOffice: %s', pedido.id, exc)

    _set_quote_send_ready(request.session, cotizacion.id, False)

    if cliente_notificado:
        messages.success(
            request,
            _('Sales order #%(id)s was generated successfully and the customer was notified.') % {'id': pedido.id},
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
        total = Decimal('0')

        for item in list(cotizacion.items.select_related('presentacion__producto')):
            marked_delete = bool(post_data and post_data.get(f'eliminar_{item.id}'))
            quantity = _parse_quantity(
                post_data.get(f'cantidad_{item.id}') if post_data else item.cantidad,
                item.cantidad,
            )
            subtotal = _parse_decimal(item.precio) * quantity

            rows.append({
                'item': item,
                'quantity': quantity,
                'subtotal': subtotal,
                'marked_delete': marked_delete,
            })

            if marked_delete:
                continue

            total += subtotal
            items_payload.append({
                'presentacion': item.presentacion,
                'cantidad': quantity,
                'precio': item.precio,
            })

        return rows, items_payload, total

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
                    titulo=f'Quote cancelled #{cotizacion.id}',
                    mensaje=f'{cotizacion.cliente.nombre_empresa} cancelled the quote.',
                    tipo='COTIZACION',
                    url=f'/cotizaciones/backoffice/{cotizacion.id}/',
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



