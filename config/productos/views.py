from django.core.cache import cache
from django.core.paginator import Paginator
import json
from django.db.models import Prefetch, Q
from django.db.utils import IntegrityError
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from .models import (
    Producto,
    Categoria,
    Marca,
    Presentacion,
    ConfiguracionPrecios,
    ConfiguracionDescuentos,
    Promocion,
    normalize_codigo_barras,
)
from .promotions import (
    adjuntar_promociones_a_productos,
    opciones_monto_fijo_promocion,
    opciones_porcentaje_promocion,
    promociones_activas_queryset,
)
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.utils.translation import gettext as _, get_language
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion
from config.productos.packaging import parse_case_packaging_from_product_name
from config.usuarios.permissions import internal_permission_required


CATALOGO_CACHE_TIMEOUT = 60
CATALOGO_PAGE_SIZE = 50


def _is_admin_user(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.role == "admin"))


def _parse_decimal(value, default="0"):
    text = str(value or "").strip().replace(",", ".")
    if not text:
        text = str(default)
    try:
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal(str(default)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _format_decimal(value):
    return format(_parse_decimal(value), ".2f")


def _parse_optional_decimal(value):
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _parse_positive_int(value, *, default=1):
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _parse_non_negative_int(value, *, default=0):
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _parse_optional_positive_int(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _post_list(request, base_name):
    values = request.POST.getlist(base_name)
    if values:
        return values
    return request.POST.getlist(f"{base_name}[]")


def _parse_new_presentacion_rows_from_post(request):
    nombres = _post_list(request, "presentacion_nueva_nombre")
    tipos = _post_list(request, "presentacion_nueva_tipo_contenido")
    unidades_list = _post_list(request, "presentacion_nueva_unidades")
    costos = _post_list(request, "presentacion_nueva_costo")
    stocks = _post_list(request, "presentacion_nueva_stock")
    pesos = _post_list(request, "presentacion_nueva_peso_por_caja")
    pallet_ties = _post_list(request, "presentacion_nueva_pallet_tie")
    pallet_highs = _post_list(request, "presentacion_nueva_pallet_high")

    rows = []
    for index, raw_nombre in enumerate(nombres):
        nombre = (raw_nombre or "").strip()
        tipo_contenido = (tipos[index] if index < len(tipos) else "unidades").strip() or "unidades"
        unidades_raw = (unidades_list[index] if index < len(unidades_list) else "").strip()
        costo_raw = (costos[index] if index < len(costos) else "").strip()
        stock_raw = (stocks[index] if index < len(stocks) else "").strip()
        peso_raw = (pesos[index] if index < len(pesos) else "").strip()
        pallet_tie_raw = (pallet_ties[index] if index < len(pallet_ties) else "").strip()
        pallet_high_raw = (pallet_highs[index] if index < len(pallet_highs) else "").strip()

        if not any([nombre, unidades_raw, costo_raw, stock_raw, peso_raw, pallet_tie_raw, pallet_high_raw]):
            continue

        rows.append({
            "nombre": nombre or _("Presentation %(number)s") % {"number": len(rows) + 1},
            "tipo_contenido": tipo_contenido,
            "unidades": _parse_positive_int(unidades_raw, default=1),
            "costo": _parse_optional_decimal(costo_raw),
            "peso_por_caja": _parse_optional_decimal(peso_raw),
            "pallet_tie": _parse_optional_positive_int(pallet_tie_raw),
            "pallet_high": _parse_optional_positive_int(pallet_high_raw),
            "stock_inicial": _parse_non_negative_int(stock_raw, default=0),
        })
    return rows


def _apply_initial_stock_for_presentacion(presentacion, cantidad, *, creado_por=None):
    if cantidad <= 0:
        return
    from config.inventario.services import registrar_entrada_manual

    registrar_entrada_manual(
        presentacion=presentacion,
        cantidad=cantidad,
        observacion=_("Initial stock from product setup"),
        creado_por=creado_por,
    )


def _create_presentaciones_for_producto(producto, rows, *, creado_por=None):
    from config.productos.packaging import apply_case_packaging_defaults_to_presentacion

    created = []
    for row in rows:
        stock_inicial = row.pop("stock_inicial", 0)
        presentacion = Presentacion(producto=producto, **row)
        apply_case_packaging_defaults_to_presentacion(presentacion, producto.nombre, overwrite=False)
        presentacion.save()
        _apply_initial_stock_for_presentacion(presentacion, stock_inicial, creado_por=creado_por)
        created.append(presentacion)
    return created


def _apply_additional_stock_from_post(request, producto):
    from config.inventario.services import registrar_entrada_manual

    for presentacion in producto.presentaciones.all():
        stock_add = (request.POST.get(f"stock_adicional_{presentacion.id}") or "").strip()
        if not stock_add:
            continue
        qty = _parse_non_negative_int(stock_add, default=0)
        if qty <= 0:
            continue
        registrar_entrada_manual(
            presentacion=presentacion,
            cantidad=qty,
            observacion=_("Manual stock adjustment from product edit"),
            creado_por=request.user,
        )


def _delete_presentaciones_for_producto(producto, presentacion_ids, *, request=None):
    from django.db.models.deletion import ProtectedError

    for raw_id in presentacion_ids:
        try:
            presentacion_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        presentacion = Presentacion.objects.filter(pk=presentacion_id, producto=producto).first()
        if presentacion is None:
            continue
        try:
            presentacion.delete()
        except ProtectedError:
            if request is not None:
                messages.error(
                    request,
                    _('Could not delete presentation "%(name)s" because it is already used in orders or inventory.') % {
                        "name": presentacion.nombre,
                    },
                )


def _get_price_margin_config():
    return ConfiguracionPrecios.obtener()


def _get_price_margin_values():
    return [_format_decimal(porcentaje) for porcentaje in _get_price_margin_config().porcentajes_lista()]


def _get_margin_values_from_config(configuracion):
    return [
        _format_decimal(configuracion.porcentaje_1),
        _format_decimal(configuracion.porcentaje_2),
        _format_decimal(configuracion.porcentaje_3),
        _format_decimal(configuracion.porcentaje_4),
        _format_decimal(configuracion.porcentaje_5),
    ]


def _recalcular_presentaciones_con_costo():
    for presentacion in Presentacion.objects.exclude(costo__isnull=True):
        presentacion.save()


def _presentaciones_prefetch():
    return Prefetch(
        "presentaciones",
        queryset=Presentacion.objects.only(
            "id",
            "producto_id",
            "nombre",
            "nombre_en",
            "unidades",
            "tipo_contenido",
            "tipo_contenido_en",
            "costo",
            "precio_1",
            "precio_2",
            "precio_3",
            "precio_4",
            "precio_5",
        ).order_by("id"),
    )


def _get_cliente_price_tier(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    if getattr(user, 'role', '') != 'cliente':
        return None

    try:
        cliente = Cliente.objects.only('nivel_precio', 'estado_revision').get(usuario=user)
    except Cliente.DoesNotExist:
        return None

    if cliente.estado_revision != Cliente.REVIEW_STATUS_APPROVED:
        return None

    return cliente.get_nivel_precio_normalizado()


def _refresh_presentacion_prices(presentacion):
    if presentacion.costo is not None:
        presentacion.recalcular_precios()


def _hydrate_productos(productos):
    for producto in productos:
        presentaciones = list(producto.presentaciones.all())
        for presentacion in presentaciones:
            presentacion.producto = producto
            _refresh_presentacion_prices(presentacion)
        producto.presentaciones_prefetch = presentaciones
        producto.primera_presentacion = presentaciones[0] if presentaciones else None
    return productos


def _catalog_display_name(*, nombre, nombre_en=''):
    if get_language() == 'en' and nombre_en:
        return nombre_en
    return nombre


def _catalog_sort_key(value):
    return str(value or '').casefold()


def _sort_catalog_productos(productos):
    return sorted(
        productos,
        key=lambda producto: (
            _catalog_sort_key(_catalog_display_name(nombre=producto.nombre, nombre_en=producto.nombre_en)),
            producto.id,
        ),
    )


def _sort_catalog_categorias(categorias):
    return sorted(
        categorias,
        key=lambda categoria: (
            _catalog_sort_key(_catalog_display_name(nombre=categoria.nombre, nombre_en=categoria.nombre_en)),
            categoria.id,
        ),
    )


def _sort_catalog_marcas(marcas):
    return sorted(
        marcas,
        key=lambda marca: (
            _catalog_sort_key(_catalog_display_name(nombre=marca.nombre, nombre_en=marca.nombre_en)),
            marca.id,
        ),
    )


def _get_cached_catalogo_productos():
    cache_key = "catalogo:productos_activos_v2"
    productos = cache.get(cache_key)
    if productos is None:
        productos = _hydrate_productos(list(
            Producto.objects.filter(activo=True).select_related("categoria", "marca").only(
                "id",
                "nombre",
                "nombre_en",
                "imagen",
                "categoria_id",
                "marca_id",
            ).prefetch_related(_presentaciones_prefetch()).order_by("nombre", "id")
        ))
        cache.set(cache_key, productos, CATALOGO_CACHE_TIMEOUT)
    return productos


def _get_cached_catalogo_categorias():
    cache_key = "catalogo:categorias_v2"
    categorias = cache.get(cache_key)
    if categorias is None:
        categorias = list(Categoria.objects.only("id", "nombre", "nombre_en").order_by("nombre", "id"))
        cache.set(cache_key, categorias, CATALOGO_CACHE_TIMEOUT)
    return categorias


def _get_cached_catalogo_marcas():
    cache_key = "catalogo:marcas_activas_v2"
    marcas = cache.get(cache_key)
    if marcas is None:
        marcas = list(
            Marca.objects.filter(activo=True).only(
                "id",
                "nombre",
                "nombre_en",
                "activo",
                "logo",
            ).prefetch_related("categorias").order_by("nombre", "id")
        )
        for marca in marcas:
            marca.categorias_ids = " ".join(str(categoria.id) for categoria in marca.categorias.all())
        cache.set(cache_key, marcas, CATALOGO_CACHE_TIMEOUT)
    return marcas


def _catalogo_public_filter_params(request):
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
    if request.GET.get('promociones') == '1':
        params['promociones'] = '1'
    if request.GET.get('guest') == '1':
        params['guest'] = '1'
    return params


def _catalogo_public_productos_queryset(request):
    queryset = (
        Producto.objects.filter(activo=True)
        .select_related('categoria', 'marca')
        .only(
            'id',
            'nombre',
            'nombre_en',
            'imagen',
            'categoria_id',
            'marca_id',
        )
        .prefetch_related(_presentaciones_prefetch())
        .order_by('nombre', 'id')
    )

    filters = _catalogo_public_filter_params(request)
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
    if filters.get('promociones'):
        promo_product_ids = promociones_activas_queryset().values('producto_id')
        queryset = queryset.filter(id__in=promo_product_ids)
    return queryset


def catalogo(request):
    force_guest_mode = request.GET.get("guest") == "1"
    is_authenticated = bool(request.user.is_authenticated)
    is_cliente = bool(is_authenticated and getattr(request.user, 'role', '') == 'cliente')
    can_quote = bool(is_authenticated and not force_guest_mode)
    client_price_tier = _get_cliente_price_tier(request.user)
    can_view_received_quotes = bool(
        is_authenticated
        and not force_guest_mode
        and is_cliente
    )
    pendientes_cotizaciones = 0

    if can_view_received_quotes:
        try:
            cliente = Cliente.objects.only('id').get(usuario=request.user)
            pendientes_cotizaciones = Cotizacion.objects.filter(
                cliente=cliente,
                estado='LISTA_PARA_CONFIRMACION',
            ).count()
        except Cliente.DoesNotExist:
            can_view_received_quotes = False

    catalogo_url = reverse("catalogo")
    if force_guest_mode:
        catalogo_url = f"{catalogo_url}?guest=1"
    promociones_url = reverse("catalogo")
    promociones_query = ["promociones=1"]
    if force_guest_mode:
        promociones_query.append("guest=1")
    promociones_url = f"{promociones_url}?{'&'.join(promociones_query)}"

    filter_params = _catalogo_public_filter_params(request)
    promociones_disponibles = promociones_activas_queryset().exists()
    paginator = Paginator(_catalogo_public_productos_queryset(request), CATALOGO_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    productos = adjuntar_promociones_a_productos(_hydrate_productos(list(page_obj.object_list)))
    categorias = _sort_catalog_categorias(_get_cached_catalogo_categorias())
    marcas = _sort_catalog_marcas(_get_cached_catalogo_marcas())
    carrito_session = request.session.get('carrito', {}) or {}
    carrito_total_items = sum(int(item.get('cantidad') or 0) for item in carrito_session.values())

    context = {
        'productos': productos,
        'page_obj': page_obj,
        'filter_q': filter_params.get('q', ''),
        'filter_categoria': filter_params.get('categoria', ''),
        'filter_marca': filter_params.get('marca', ''),
        'filter_promociones': filter_params.get('promociones', ''),
        'categorias': categorias,
        'marcas': marcas,
        'guest_mode': force_guest_mode,
        'can_quote': can_quote,
        'can_view_received_quotes': can_view_received_quotes,
        'pendientes_cotizaciones': pendientes_cotizaciones,
        'catalogo_url': catalogo_url,
        'promociones_url': promociones_url,
        'promociones_disponibles': promociones_disponibles,
        'client_price_tier': client_price_tier,
        'show_client_prices': client_price_tier is not None,
        'carrito_total_items': carrito_total_items,
    }

    response = render(request, 'productos/catalogo.html', context)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response

ADMIN_PRODUCTOS_PAGE_SIZE = 50


def _productos_admin_filter_params(request):
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


def _productos_admin_queryset(request):
    queryset = (
        Producto.objects.select_related('categoria', 'marca')
        .only(
            'id',
            'nombre',
            'nombre_en',
            'codigo_barras',
            'imagen',
            'activo',
            'categoria_id',
            'marca_id',
            'categoria__id',
            'categoria__nombre',
            'categoria__nombre_en',
            'marca__id',
            'marca__nombre',
            'marca__nombre_en',
        )
        .order_by('nombre', 'id')
    )

    filters = _productos_admin_filter_params(request)
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
@internal_permission_required('admin.products.view')
def lista_productos(request):
    filter_params = _productos_admin_filter_params(request)
    paginator = Paginator(_productos_admin_queryset(request), ADMIN_PRODUCTOS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'admin/productos.html', {
        'productos': page_obj.object_list,
        'page_obj': page_obj,
        'filter_params': filter_params,
        'filter_q': filter_params.get('q', ''),
        'filter_categoria': filter_params.get('categoria', ''),
        'filter_marca': filter_params.get('marca', ''),
        'categorias': Categoria.objects.only('id', 'nombre', 'nombre_en').order_by('nombre'),
        'marcas': Marca.objects.only('id', 'nombre', 'nombre_en').prefetch_related(
            Prefetch('categorias', queryset=Categoria.objects.only('id'))
        ).order_by('nombre'),
        'price_margins': _get_price_margin_values(),
    })


@login_required
@internal_permission_required('admin.products.view')
def lista_marcas(request):

    marcas = Marca.objects.all().prefetch_related('categorias')

    return render(request, 'admin/marcas.html', {
        'marcas': marcas,
    })

@login_required
@internal_permission_required('admin.products.manage')
def crear_producto(request):

    if request.method == "POST":

        nombre = (request.POST.get("nombre") or "").strip()
        nombre_en = (request.POST.get("nombre_en") or "").strip()
        codigo_barras = normalize_codigo_barras(request.POST.get("codigo_barras"))
        descripcion = request.POST.get("descripcion") or ""
        descripcion_en = (request.POST.get("descripcion_en") or "").strip()

        categoria_id = request.POST.get("categoria")
        marca_id = request.POST.get("marca")

        imagen = request.FILES.get("imagen")

        activo = True if request.POST.get("activo") else False
        destacado = True if request.POST.get("destacado") else False

        descuento = request.POST.get("descuento") or 0

        categoria = None
        marca = None

        if categoria_id:
            categoria = Categoria.objects.get(id=categoria_id)

        if marca_id:
            marca = Marca.objects.get(id=marca_id)

        try:
            producto = Producto.objects.create(
                nombre=nombre,
                nombre_en=nombre_en,
                codigo_barras=codigo_barras,
                descripcion=descripcion,
                descripcion_en=descripcion_en,
                categoria=categoria,
                marca=marca,
                imagen=imagen,
                activo=activo,
                destacado=destacado,
                descuento=descuento
            )
        except IntegrityError:
            messages.error(
                request,
                _("Could not create the product because the barcode is already used by another product."),
            )
            return redirect("crear_producto")

        _create_presentaciones_for_producto(
            producto,
            _parse_new_presentacion_rows_from_post(request),
            creado_por=request.user,
        )

        messages.success(request, _("Product created successfully."))
        return redirect("editar_producto", producto_id=producto.id)

    context = {
        "categorias": Categoria.objects.all(),
        "marcas": Marca.objects.all(),
        "price_margins": _get_price_margin_values(),
    }

    return render(request, "admin/crear_producto.html", context)


@login_required
@internal_permission_required('admin.products.manage')
def configurar_precios(request):
    configuracion = _get_price_margin_config()
    price_margins = _get_margin_values_from_config(configuracion)

    if request.method == "POST":
        configuracion.porcentaje_1 = _parse_decimal(request.POST.get("porcentaje_1"), configuracion.porcentaje_1)
        configuracion.porcentaje_2 = _parse_decimal(request.POST.get("porcentaje_2"), configuracion.porcentaje_2)
        configuracion.porcentaje_3 = _parse_decimal(request.POST.get("porcentaje_3"), configuracion.porcentaje_3)
        configuracion.porcentaje_4 = _parse_decimal(request.POST.get("porcentaje_4"), configuracion.porcentaje_4)
        configuracion.porcentaje_5 = _parse_decimal(request.POST.get("porcentaje_5"), configuracion.porcentaje_5)
        price_margins = _get_margin_values_from_config(configuracion)
        try:
            configuracion.save()
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
            return render(request, "admin/configurar_precios.html", {
                "configuracion": configuracion,
                "price_margins": price_margins,
            })

        _recalcular_presentaciones_con_costo()
        from config.auditoria.business_events import log_business_event
        from config.auditoria.models import AuditLog
        log_business_event(
            request.user,
            action_label=_('Updated global price margin percentages'),
            action_category=AuditLog.CATEGORY_UPDATE,
            entity_type='Pricing',
            entity_label=_('Price margins P1-P5'),
            metadata={'margins': price_margins},
            request=request,
        )
        messages.success(request, _("Price percentages updated successfully"))
        return redirect("configurar_precios")

    return render(request, "admin/configurar_precios.html", {
        "configuracion": configuracion,
        "price_margins": price_margins,
    })


def _get_discount_preset_config():
    return ConfiguracionDescuentos.obtener()


def _get_discount_preset_values(configuracion=None):
    configuracion = configuracion or _get_discount_preset_config()
    return [_format_decimal(amount) for amount in configuracion.descuentos_lista()]


@login_required
@internal_permission_required('admin.products.manage')
def configurar_descuentos(request):
    configuracion = _get_discount_preset_config()
    discount_presets = _get_discount_preset_values(configuracion)

    if request.method == "POST":
        for index in range(1, 11):
            field_name = f"descuento_{index}"
            setattr(
                configuracion,
                field_name,
                _parse_decimal(request.POST.get(field_name), getattr(configuracion, field_name)),
            )
        discount_presets = _get_discount_preset_values(configuracion)
        try:
            configuracion.save()
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
            return render(request, "admin/configurar_descuentos.html", {
                "configuracion": configuracion,
                "discount_presets": discount_presets,
            })

        messages.success(request, _("Preset discounts updated successfully."))
        from config.auditoria.business_events import log_business_event
        from config.auditoria.models import AuditLog
        log_business_event(
            request.user,
            action_label=_('Updated preset discount amounts'),
            action_category=AuditLog.CATEGORY_UPDATE,
            entity_type='Pricing',
            entity_label=_('Preset discounts'),
            metadata={'discount_presets': discount_presets},
            request=request,
        )
        return redirect("configurar_descuentos")

    return render(request, "admin/configurar_descuentos.html", {
        "configuracion": configuracion,
        "discount_presets": discount_presets,
    })


@login_required
@internal_permission_required('admin.products.manage')
def crear_categoria(request):

    if request.method == "POST":
        nombre = (request.POST.get("nombre") or "").strip()
        nombre_en = (request.POST.get("nombre_en") or "").strip()
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        if not nombre:
            if is_ajax:
                return JsonResponse({"error": _("El nombre de la categoria es obligatorio.")}, status=400)
            return render(request, "admin/crear_categoria.html", {
                "error": _("El nombre de la categoria es obligatorio."),
                "nombre": nombre,
                "nombre_en": nombre_en,
            })

        if Categoria.objects.filter(nombre__iexact=nombre).exists():
            if is_ajax:
                return JsonResponse({"error": _("Ya existe una categoria con ese nombre.")}, status=400)
            return render(request, "admin/crear_categoria.html", {
                "error": _("Ya existe una categoria con ese nombre."),
                "nombre": nombre,
                "nombre_en": nombre_en,
            })

        categoria = Categoria.objects.create(
            nombre=nombre,
            nombre_en=nombre_en,
        )
        if is_ajax:
            return JsonResponse({"id": categoria.id, "nombre": categoria.nombre, "nombre_en": categoria.nombre_en})
        messages.success(request, _("Categoria creada correctamente"))
        return redirect("lista_productos")

    return render(request, "admin/crear_categoria.html")


@login_required
@internal_permission_required('admin.products.manage')
def crear_marca(request):

    categorias = Categoria.objects.all()

    if request.method == "POST":
        nombre = (request.POST.get("nombre") or "").strip()
        nombre_en = (request.POST.get("nombre_en") or "").strip()
        logo = request.FILES.get("logo")
        categorias_ids = request.POST.getlist("categorias")
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        if not nombre:
            if is_ajax:
                return JsonResponse({"error": _("El nombre de la marca es obligatorio.")}, status=400)
            return render(request, "admin/crear_marca.html", {
                "error": _("El nombre de la marca es obligatorio."),
                "categorias": categorias,
                "selected_categorias": [str(cid) for cid in categorias_ids],
                "nombre": nombre,
                "nombre_en": nombre_en,
            })

        if Marca.objects.filter(nombre__iexact=nombre).exists():
            if is_ajax:
                return JsonResponse({"error": _("Ya existe una marca con ese nombre.")}, status=400)
            return render(request, "admin/crear_marca.html", {
                "error": _("Ya existe una marca con ese nombre."),
                "categorias": categorias,
                "selected_categorias": [str(cid) for cid in categorias_ids],
                "nombre": nombre,
                "nombre_en": nombre_en,
            })

        marca = Marca.objects.create(
            nombre=nombre,
            nombre_en=nombre_en,
            logo=logo,
        )

        if categorias_ids:
            marca.categorias.set(categorias_ids)

        if is_ajax:
            return JsonResponse({"id": marca.id, "nombre": marca.nombre, "nombre_en": marca.nombre_en})

        messages.success(request, _("Marca creada correctamente"))
        return redirect("lista_marcas")

    return render(request, "admin/crear_marca.html", {
        "categorias": categorias,
        "selected_categorias": [],
    })


@login_required
@internal_permission_required('admin.products.manage')
def editar_marca(request, marca_id):

    marca = get_object_or_404(Marca, id=marca_id)
    categorias = Categoria.objects.all()

    if request.method == "POST":
        nombre = (request.POST.get("nombre") or "").strip()
        nombre_en = (request.POST.get("nombre_en") or "").strip()
        logo = request.FILES.get("logo")
        categorias_ids = request.POST.getlist("categorias")

        if not nombre:
            return render(request, "admin/editar_marca.html", {
                "error": _("El nombre de la marca es obligatorio."),
                "marca": marca,
                "categorias": categorias,
                "selected_categorias": [str(cid) for cid in categorias_ids],
            })

        duplicated = Marca.objects.filter(nombre__iexact=nombre).exclude(id=marca.id).exists()
        if duplicated:
            return render(request, "admin/editar_marca.html", {
                "error": _("Ya existe otra marca con ese nombre."),
                "marca": marca,
                "categorias": categorias,
                "selected_categorias": [str(cid) for cid in categorias_ids],
            })

        marca.nombre = nombre
        marca.nombre_en = nombre_en
        if logo:
            if marca.logo:
                marca.logo.delete(save=False)
            marca.logo = logo
        marca.save()
        marca.categorias.set(categorias_ids)

        messages.success(request, _("Marca actualizada correctamente"))
        return redirect("lista_marcas")

    selected_categorias = [str(c.id) for c in marca.categorias.all()]

    return render(request, "admin/editar_marca.html", {
        "marca": marca,
        "categorias": categorias,
        "selected_categorias": selected_categorias,
    })


@login_required
@internal_permission_required('admin.products.manage')
def desactivar_marca(request, marca_id):

    marca = get_object_or_404(Marca, id=marca_id)
    marca.activo = False
    marca.save()

    messages.success(request, _("Marca inhabilitada"))
    return redirect('lista_marcas')


@login_required
@internal_permission_required('admin.products.manage')
def activar_marca(request, marca_id):

    marca = get_object_or_404(Marca, id=marca_id)
    marca.activo = True
    marca.save()

    messages.success(request, _("Marca activada"))
    return redirect('lista_marcas')

@login_required
@internal_permission_required('admin.products.manage')
def desactivar_producto(request, producto_id):

    producto = get_object_or_404(Producto, id=producto_id)

    producto.activo = False
    producto.save()

    return redirect('lista_productos')

@login_required
@internal_permission_required('admin.products.manage')
def activar_producto(request, producto_id):

    producto = get_object_or_404(Producto, id=producto_id)

    producto.activo = True
    producto.save()

    return redirect('lista_productos')

@login_required
@internal_permission_required('admin.products.manage')
def editar_producto(request, producto_id):

    producto = get_object_or_404(Producto, id=producto_id)

    categorias = Categoria.objects.all()
    marcas = Marca.objects.all()

    presentaciones = producto.presentaciones.select_related("stock_operativo").all()

    if request.method == "POST":

        # -------- PRODUCTO --------

        producto.nombre = (request.POST.get("nombre") or "").strip()
        producto.nombre_en = (request.POST.get("nombre_en") or "").strip()
        producto.codigo_barras = normalize_codigo_barras(request.POST.get("codigo_barras"))
        producto.descripcion = request.POST.get("descripcion") or ""
        producto.descripcion_en = (request.POST.get("descripcion_en") or "").strip()

        categoria_id = request.POST.get("categoria")
        marca_id = request.POST.get("marca")

        if categoria_id:
            producto.categoria_id = categoria_id

        if marca_id:
            producto.marca_id = marca_id

        if request.FILES.get("imagen"):
            if producto.imagen:
                producto.imagen.delete(save=False)
            producto.imagen = request.FILES.get("imagen")

        producto.activo = True if request.POST.get("activo") else False
        producto.destacado = True if request.POST.get("destacado") else False

        producto.descuento = request.POST.get("descuento") or 0

        try:
            producto.save()
        except IntegrityError:
            messages.error(
                request,
                _("Could not save the product because the barcode is already used by another product."),
            )
            return redirect("editar_producto", producto_id=producto.id)

        # -------- PRESENTACIONES --------

        eliminar_ids = set(request.POST.getlist("presentacion_eliminar"))

        for presentacion in producto.presentaciones.all():
            if str(presentacion.id) in eliminar_ids:
                continue

            presentacion.nombre = request.POST.get(f"presentacion_nombre_{presentacion.id}")
            presentacion.tipo_contenido = request.POST.get(f"tipo_contenido_{presentacion.id}")

            unidades = request.POST.get(f"unidades_{presentacion.id}")
            if unidades:
                presentacion.unidades = _parse_positive_int(unidades, default=presentacion.unidades or 1)

            if f"costo_{presentacion.id}" in request.POST:
                presentacion.costo = _parse_optional_decimal(request.POST.get(f"costo_{presentacion.id}"))

            if f"peso_por_caja_{presentacion.id}" in request.POST:
                presentacion.peso_por_caja = _parse_optional_decimal(request.POST.get(f"peso_por_caja_{presentacion.id}"))

            if f"pallet_tie_{presentacion.id}" in request.POST:
                presentacion.pallet_tie = _parse_optional_positive_int(request.POST.get(f"pallet_tie_{presentacion.id}"))

            if f"pallet_high_{presentacion.id}" in request.POST:
                presentacion.pallet_high = _parse_optional_positive_int(request.POST.get(f"pallet_high_{presentacion.id}"))

            presentacion.save()

        _delete_presentaciones_for_producto(producto, eliminar_ids, request=request)
        _create_presentaciones_for_producto(
            producto,
            _parse_new_presentacion_rows_from_post(request),
            creado_por=request.user,
        )
        _apply_additional_stock_from_post(request, producto)

        messages.success(request, _("Product updated successfully."))
        return redirect("editar_producto", producto_id=producto.id)
    
    return render(request, "admin/editar_producto.html", {
        "producto": producto,
        "categorias": categorias,
        "marcas": marcas,
        "presentaciones": presentaciones,
        "price_margins": _get_price_margin_values(),
        "packaging_defaults_json": json.dumps(
            parse_case_packaging_from_product_name(producto.nombre) or {},
            ensure_ascii=False,
        ),
    })


@login_required
@internal_permission_required("admin.products.manage")
def parse_packaging_from_name(request):
    nombre = (request.GET.get("nombre") or "").strip()
    if not nombre:
        return JsonResponse(
            {"ok": False, "error": _("Product name is required.")},
            status=400,
        )

    parsed = parse_case_packaging_from_product_name(nombre)
    if not parsed:
        return JsonResponse(
            {
                "ok": False,
                "error": _(
                    "No case/box pattern detected in the product name. "
                    "Try formats like 12/16.9 LT, 6 CT, or 1 GALON."
                ),
            },
        )

    return JsonResponse({"ok": True, "defaults": parsed})


def _parse_optional_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    # HTML datetime-local: YYYY-MM-DDTHH:MM
    from datetime import datetime

    from django.utils import timezone as dj_timezone

    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            if dj_timezone.is_naive(parsed):
                return dj_timezone.make_aware(parsed, dj_timezone.get_current_timezone())
            return parsed
        except ValueError:
            continue
    raise ValidationError(_("Enter a valid date/time."))


def _promocion_form_context(promocion=None, *, post=None, error=None):
    productos = Producto.objects.filter(activo=True).order_by("nombre")
    presentaciones = Presentacion.objects.select_related("producto").order_by("producto__nombre", "nombre")
    data = {
        "nombre": "",
        "descripcion": "",
        "producto_id": "",
        "presentacion_id": "",
        "cantidad_minima": "1",
        "tipo_beneficio": Promocion.TIPO_PERCENT,
        "valor_beneficio": "",
        "fecha_inicio": "",
        "fecha_fin": "",
        "activa": True,
    }
    if promocion is not None:
        data.update({
            "nombre": promocion.nombre,
            "descripcion": promocion.descripcion,
            "producto_id": str(promocion.producto_id),
            "presentacion_id": str(promocion.presentacion_id or ""),
            "cantidad_minima": str(promocion.cantidad_minima),
            "tipo_beneficio": promocion.tipo_beneficio,
            "valor_beneficio": format(promocion.valor_beneficio, ".2f"),
            "fecha_inicio": promocion.fecha_inicio.strftime("%Y-%m-%dT%H:%M") if promocion.fecha_inicio else "",
            "fecha_fin": promocion.fecha_fin.strftime("%Y-%m-%dT%H:%M") if promocion.fecha_fin else "",
            "activa": promocion.activa,
        })
    if post is not None:
        data.update({
            "nombre": (post.get("nombre") or "").strip(),
            "descripcion": (post.get("descripcion") or "").strip(),
            "producto_id": (post.get("producto") or "").strip(),
            "presentacion_id": (post.get("presentacion") or "").strip(),
            "cantidad_minima": (post.get("cantidad_minima") or "1").strip(),
            "tipo_beneficio": (post.get("tipo_beneficio") or Promocion.TIPO_PERCENT).strip(),
            "valor_beneficio": (post.get("valor_beneficio") or "").strip(),
            "fecha_inicio": (post.get("fecha_inicio") or "").strip(),
            "fecha_fin": (post.get("fecha_fin") or "").strip(),
            "activa": bool(post.get("activa")),
        })

    valor_actual = data.get("valor_beneficio") or None
    return {
        "promocion": promocion,
        "productos": productos,
        "presentaciones": presentaciones,
        "tipos_beneficio": Promocion.TIPO_BENEFICIO_CHOICES,
        "percentage_preset_options": opciones_porcentaje_promocion(valor_actual),
        "fixed_preset_options": opciones_monto_fijo_promocion(valor_actual),
        "error": error,
        **data,
    }


def _build_promocion_from_post(post, promocion=None):
    promocion = promocion or Promocion()
    promocion.nombre = (post.get("nombre") or "").strip()
    promocion.descripcion = (post.get("descripcion") or "").strip()
    producto_id = (post.get("producto") or "").strip()
    presentacion_id = (post.get("presentacion") or "").strip()
    if not producto_id:
        raise ValidationError(_("Product is required."))
    promocion.producto = get_object_or_404(Producto, id=producto_id)
    promocion.presentacion = get_object_or_404(Presentacion, id=presentacion_id) if presentacion_id else None
    try:
        promocion.cantidad_minima = int(post.get("cantidad_minima") or 1)
    except (TypeError, ValueError) as exc:
        raise ValidationError(_("Minimum quantity must be a whole number.")) from exc
    promocion.tipo_beneficio = (post.get("tipo_beneficio") or Promocion.TIPO_PERCENT).strip()
    if promocion.tipo_beneficio not in {Promocion.TIPO_PERCENT, Promocion.TIPO_FIXED}:
        raise ValidationError(_("Invalid benefit type."))
    valor_raw = (post.get("valor_beneficio") or "").strip()
    if not valor_raw:
        raise ValidationError(_("Benefit value is required."))
    promocion.valor_beneficio = _parse_decimal(valor_raw, "0")
    if promocion.tipo_beneficio == Promocion.TIPO_PERCENT:
        # Only preset % values are allowed; when editing, keep a legacy/custom value selectable.
        legacy_value = None
        if promocion.pk:
            legacy_value = (
                Promocion.objects.filter(pk=promocion.pk)
                .values_list("valor_beneficio", flat=True)
                .first()
            )
        allowed = {option["value"] for option in opciones_porcentaje_promocion(legacy_value)}
        if format(promocion.valor_beneficio, ".2f") not in allowed:
            raise ValidationError(_("Select one of the configured percentage discounts."))
    promocion.fecha_inicio = _parse_optional_datetime(post.get("fecha_inicio"))
    promocion.fecha_fin = _parse_optional_datetime(post.get("fecha_fin"))
    promocion.activa = bool(post.get("activa"))
    promocion.full_clean()
    return promocion


ADMIN_PROMOCIONES_PAGE_SIZE = 50


def _promociones_admin_filter_params(request):
    params = {}
    query = str(request.GET.get("q") or "").strip()
    if query:
        params["q"] = query

    estado = str(request.GET.get("estado") or "activas").strip().lower()
    if estado not in {"activas", "inactivas"}:
        estado = "activas"
    params["estado"] = estado

    producto_id = str(request.GET.get("producto") or "").strip()
    if producto_id.isdigit():
        params["producto"] = producto_id

    tipo = str(request.GET.get("tipo") or "").strip().upper()
    if tipo in {Promocion.TIPO_PERCENT, Promocion.TIPO_FIXED}:
        params["tipo"] = tipo

    return params


def _promociones_admin_queryset(request):
    filters = _promociones_admin_filter_params(request)
    queryset = (
        Promocion.objects.select_related("producto", "presentacion")
        .order_by("-creada_en", "id")
    )

    if filters["estado"] == "activas":
        queryset = queryset.filter(activa=True)
    else:
        queryset = queryset.filter(activa=False)

    query = filters.get("q")
    if query:
        queryset = queryset.filter(
            Q(nombre__icontains=query)
            | Q(descripcion__icontains=query)
            | Q(producto__nombre__icontains=query)
            | Q(producto__nombre_en__icontains=query)
            | Q(presentacion__nombre__icontains=query)
        )

    if filters.get("producto"):
        queryset = queryset.filter(producto_id=filters["producto"])

    if filters.get("tipo"):
        queryset = queryset.filter(tipo_beneficio=filters["tipo"])

    return queryset, filters


def _promociones_list_redirect(request, *, estado=None):
    from urllib.parse import urlencode

    params = _promociones_admin_filter_params(request)
    if estado in {"activas", "inactivas"}:
        params["estado"] = estado
    query = urlencode(params)
    url = reverse("lista_promociones")
    return redirect(f"{url}?{query}" if query else url)


@login_required
@internal_permission_required("admin.products.view")
def lista_promociones(request):
    queryset, filter_params = _promociones_admin_queryset(request)
    paginator = Paginator(queryset, ADMIN_PROMOCIONES_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    active_count = Promocion.objects.filter(activa=True).count()
    inactive_count = Promocion.objects.filter(activa=False).count()

    return render(request, "admin/promociones.html", {
        "promociones": page_obj.object_list,
        "page_obj": page_obj,
        "filter_params": filter_params,
        "filter_q": filter_params.get("q", ""),
        "filter_estado": filter_params.get("estado", "activas"),
        "filter_producto": filter_params.get("producto", ""),
        "filter_tipo": filter_params.get("tipo", ""),
        "active_count": active_count,
        "inactive_count": inactive_count,
        "productos": Producto.objects.only("id", "nombre").order_by("nombre"),
        "tipos_beneficio": Promocion.TIPO_BENEFICIO_CHOICES,
    })


@login_required
@internal_permission_required("admin.products.manage")
def crear_promocion(request):
    if request.method == "POST":
        try:
            promocion = _build_promocion_from_post(request.POST)
            promocion.save()
            messages.success(request, _("Promotion created successfully."))
            return redirect("lista_promociones")
        except ValidationError as exc:
            error = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            return render(
                request,
                "admin/promocion_form.html",
                _promocion_form_context(post=request.POST, error=error),
            )
    return render(request, "admin/promocion_form.html", _promocion_form_context())


@login_required
@internal_permission_required("admin.products.manage")
def editar_promocion(request, promocion_id):
    promocion = get_object_or_404(Promocion, id=promocion_id)
    if request.method == "POST":
        try:
            promocion = _build_promocion_from_post(request.POST, promocion=promocion)
            promocion.save()
            messages.success(request, _("Promotion updated successfully."))
            return redirect("lista_promociones")
        except ValidationError as exc:
            error = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            return render(
                request,
                "admin/promocion_form.html",
                _promocion_form_context(promocion, post=request.POST, error=error),
            )
    return render(request, "admin/promocion_form.html", _promocion_form_context(promocion))


@login_required
@internal_permission_required("admin.products.manage")
def desactivar_promocion(request, promocion_id):
    promocion = get_object_or_404(Promocion, id=promocion_id)
    promocion.activa = False
    promocion.save(update_fields=["activa", "actualizada_en"])
    messages.success(request, _("Promotion deactivated."))
    return _promociones_list_redirect(request, estado="inactivas")


@login_required
@internal_permission_required("admin.products.manage")
def activar_promocion(request, promocion_id):
    promocion = get_object_or_404(Promocion, id=promocion_id)
    promocion.activa = True
    promocion.save(update_fields=["activa", "actualizada_en"])
    messages.success(request, _("Promotion activated."))
    return _promociones_list_redirect(request, estado="activas")


@login_required
@internal_permission_required("admin.products.manage")
@require_http_methods(["POST"])
def eliminar_promocion(request, promocion_id):
    promocion = get_object_or_404(Promocion, id=promocion_id)
    nombre = promocion.nombre
    estado = "activas" if promocion.activa else "inactivas"
    promocion.delete()
    messages.success(request, _('Promotion "%(name)s" deleted.') % {"name": nombre})
    return _promociones_list_redirect(request, estado=estado)


