from django.core.cache import cache
from django.core.paginator import Paginator
import functools
import json
from django.db.models import Case, IntegerField, Prefetch, Q, When
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
    ConfiguracionLandedCost,
    Promocion,
    PromocionEscala,
    PromocionProducto,
    normalize_codigo_barras,
)
from .forms import PromocionForm, PromocionEscalaFormSet, PromocionProductoFormSet
from .promotions import (
    adjuntar_promociones_a_productos,
    combos_para_catalogo,
    opciones_monto_fijo_promocion,
    opciones_porcentaje_promocion,
    producto_ids_con_promocion_individual_activa,
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
from config.pedidos.client_history import load_cliente_favorite_productos
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
    landed_tipos = _post_list(request, "presentacion_nueva_landed_cost_override_tipo")
    landed_valores = _post_list(request, "presentacion_nueva_landed_cost_override_valor")

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
        landed_tipo = (landed_tipos[index] if index < len(landed_tipos) else "").strip().upper()
        landed_valor_raw = (landed_valores[index] if index < len(landed_valores) else "").strip()

        if not any([nombre, unidades_raw, costo_raw, stock_raw, peso_raw, pallet_tie_raw, pallet_high_raw, landed_valor_raw]):
            continue

        if landed_tipo not in {'PERCENT', 'FIXED'}:
            landed_tipo = ''
        rows.append({
            "nombre": nombre or _("Presentation %(number)s") % {"number": len(rows) + 1},
            "tipo_contenido": tipo_contenido,
            "unidades": _parse_positive_int(unidades_raw, default=1),
            "costo": _parse_optional_decimal(costo_raw),
            "peso_por_caja": _parse_optional_decimal(peso_raw),
            "pallet_tie": _parse_optional_positive_int(pallet_tie_raw),
            "pallet_high": _parse_optional_positive_int(pallet_high_raw),
            "landed_cost_override_tipo": landed_tipo,
            "landed_cost_override_valor": _parse_optional_decimal(landed_valor_raw) if landed_tipo else None,
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


def _catalogo_public_productos_queryset(request, cliente=None):
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
        # Promo-first mode: keep the full catalog and surface active promotions first
        # so shoppers can continue browsing without leaving the page.
        promo_product_ids = list(producto_ids_con_promocion_individual_activa(cliente=cliente))
        if promo_product_ids:
            queryset = queryset.annotate(
                _promo_first=Case(
                    When(id__in=promo_product_ids, then=0),
                    default=1,
                    output_field=IntegerField(),
                )
            ).order_by('_promo_first', 'nombre', 'id')
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
    cliente = None

    if can_view_received_quotes:
        try:
            cliente = Cliente.objects.get(usuario=request.user)
            pendientes_cotizaciones = Cotizacion.objects.filter(
                cliente=cliente,
                estado='LISTA_PARA_CONFIRMACION',
            ).count()
        except Cliente.DoesNotExist:
            can_view_received_quotes = False
            cliente = None

    catalogo_url = reverse("catalogo")
    if force_guest_mode:
        catalogo_url = f"{catalogo_url}?guest=1"
    promociones_url = reverse("catalogo")
    promociones_query = ["promociones=1"]
    if force_guest_mode:
        promociones_query.append("guest=1")
    promociones_url = f"{promociones_url}?{'&'.join(promociones_query)}"

    filter_params = _catalogo_public_filter_params(request)
    promociones_disponibles = promociones_activas_queryset(cliente=cliente).exists()
    paginator = Paginator(_catalogo_public_productos_queryset(request, cliente=cliente), CATALOGO_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    productos = adjuntar_promociones_a_productos(_hydrate_productos(list(page_obj.object_list)), cliente=cliente)
    # In promo-first mode, mark where the promotional block ends so the template
    # can show a seamless "continue browsing" divider before the rest of the catalog.
    promo_catalog_continue_index = None
    if filter_params.get('promociones'):
        for index, producto in enumerate(productos):
            if not getattr(producto, 'promocion_activa', None):
                promo_catalog_continue_index = index
                break
    categorias = _sort_catalog_categorias(_get_cached_catalogo_categorias())
    marcas = _sort_catalog_marcas(_get_cached_catalogo_marcas())
    carrito_session = request.session.get('carrito', {}) or {}
    carrito_total_items = sum(int(item.get('cantidad') or 0) for item in carrito_session.values())

    productos_favoritos = []
    if (
        page_obj.number == 1
        and cliente is not None
        and can_view_received_quotes
        and not force_guest_mode
        and not filter_params.get('promociones')
        and not filter_params.get('q')
        and not filter_params.get('categoria')
        and not filter_params.get('marca')
    ):
        productos_favoritos = load_cliente_favorite_productos(
            cliente=cliente,
            hydrate_fn=_hydrate_productos,
            attach_promos_fn=functools.partial(adjuntar_promociones_a_productos, cliente=cliente),
        )

    combos = []
    if (
        page_obj.number == 1
        and not filter_params.get('q')
        and not filter_params.get('categoria')
        and not filter_params.get('marca')
    ):
        combos = combos_para_catalogo(cliente=cliente)

    context = {
        'productos': productos,
        'productos_favoritos': productos_favoritos,
        'combos': combos,
        'page_obj': page_obj,
        'filter_q': filter_params.get('q', ''),
        'filter_categoria': filter_params.get('categoria', ''),
        'filter_marca': filter_params.get('marca', ''),
        'filter_promociones': filter_params.get('promociones', ''),
        'promo_catalog_continue_index': promo_catalog_continue_index,
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

    marcas_qs = Marca.objects.all().prefetch_related('categorias').order_by('nombre')
    marcas_activas = list(marcas_qs.filter(activo=True))
    marcas_inactivas = list(marcas_qs.filter(activo=False))
    tab = (request.GET.get('tab') or 'active').strip().lower()
    if tab not in {'active', 'inactive'}:
        tab = 'active'

    return render(request, 'admin/marcas.html', {
        'marcas': marcas_inactivas if tab == 'inactive' else marcas_activas,
        'marcas_activas': marcas_activas,
        'marcas_inactivas': marcas_inactivas,
        'tab': tab,
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
def configurar_landed_cost(request):
    configuracion = ConfiguracionLandedCost.obtener()
    if request.method == 'POST':
        tipo = (request.POST.get('tipo') or ConfiguracionLandedCost.TIPO_PERCENT).strip().upper()
        if tipo not in {ConfiguracionLandedCost.TIPO_PERCENT, ConfiguracionLandedCost.TIPO_FIXED}:
            tipo = ConfiguracionLandedCost.TIPO_PERCENT
        configuracion.tipo = tipo
        configuracion.valor = _parse_decimal(request.POST.get('valor'), configuracion.valor)
        try:
            configuracion.save()
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
            return render(request, 'admin/configurar_landed_cost.html', {'configuracion': configuracion})
        messages.success(request, _('Global Landed Cost updated successfully.'))
        return redirect('configurar_landed_cost')
    return render(request, 'admin/configurar_landed_cost.html', {'configuracion': configuracion})


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
        remove_logo = bool(request.POST.get('eliminar_logo'))
        if remove_logo and marca.logo:
            marca.logo.delete(save=False)
            marca.logo = None
        elif logo:
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

            if f"landed_cost_override_tipo_{presentacion.id}" in request.POST:
                landed_tipo = (request.POST.get(f"landed_cost_override_tipo_{presentacion.id}") or "").strip().upper()
                if landed_tipo not in {'PERCENT', 'FIXED'}:
                    landed_tipo = ''
                presentacion.landed_cost_override_tipo = landed_tipo
                presentacion.landed_cost_override_valor = (
                    _parse_optional_decimal(request.POST.get(f"landed_cost_override_valor_{presentacion.id}"))
                    if landed_tipo else None
                )

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


PRODUCTO_SEARCH_RESULTS_LIMIT = 20


@login_required
@internal_permission_required("admin.products.view", "admin.products.manage")
def buscar_productos_promocion(request):
    """
    Lightweight, paginated product search for the Promotions admin.

    Returns at most PRODUCTO_SEARCH_RESULTS_LIMIT matches instead of the full
    product catalog, so the Promotions form never has to load 1000+ options.
    Matches by name (ES/EN) or barcode/code.
    """
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    filtros = Q(nombre__icontains=query) | Q(nombre_en__icontains=query) | Q(codigo_barras__icontains=query)
    if query.isdigit():
        filtros |= Q(id=int(query))

    productos = (
        Producto.objects.filter(activo=True)
        .filter(filtros)
        .order_by("nombre")[:PRODUCTO_SEARCH_RESULTS_LIMIT]
    )

    results = [
        {
            "id": producto.id,
            "label": producto.nombre,
            "codigo_barras": producto.codigo_barras or "",
        }
        for producto in productos
    ]
    return JsonResponse({"results": results})


@login_required
@internal_permission_required("admin.products.view", "admin.products.manage")
def producto_presentaciones_promocion(request, producto_id):
    """Presentations of a single product (usually a handful), for the Promotions form."""
    presentaciones = list(
        Presentacion.objects.filter(producto_id=producto_id)
        .order_by("nombre")
        .values("id", "nombre")
    )
    return JsonResponse({"results": presentaciones})


@login_required
def combo_promocion_miembros(request, promocion_id):
    """
    Members of a combo (group) promotion, used by the catalog combo picker so a
    customer can choose how many units of each product they want. The quantities
    of every member add up together to reach the promotion minimum.
    """
    promo = get_object_or_404(
        Promocion.objects.prefetch_related(
            "escalas", "productos_grupo", "productos_grupo__producto"
        ),
        id=promocion_id,
        alcance=Promocion.ALCANCE_GRUPO,
        activa=True,
    )
    tier = _get_cliente_price_tier(request.user)
    escalas = sorted(promo.escalas.all(), key=lambda escala: escala.cantidad_minima)
    minimum = escalas[0].cantidad_minima if escalas else 0

    miembros = []
    for pp in promo.productos_grupo.all():
        producto = pp.producto
        if producto is None or not producto.activo:
            continue
        presentaciones = []
        for presentacion in Presentacion.objects.filter(producto=producto).order_by("id"):
            if pp.presentacion_id and presentacion.id != pp.presentacion_id:
                continue
            presentacion.producto = producto
            precio = presentacion.get_price_for_tier(tier) if tier else None
            presentaciones.append({
                "id": presentacion.id,
                "nombre": presentacion.nombre_empaque_cliente,
                "precio": float(precio) if precio is not None else None,
            })
        if not presentaciones:
            continue
        miembros.append({
            "producto_id": producto.id,
            "nombre": _catalog_display_name(nombre=producto.nombre, nombre_en=producto.nombre_en),
            "presentaciones": presentaciones,
        })

    return JsonResponse({
        "promocion_id": promo.id,
        "nombre": promo.nombre,
        "descripcion": promo.texto_catalogo(),
        "minimum": minimum,
        "escalas": [
            {"minimo": escala.cantidad_minima, "beneficio": escala.texto_beneficio()}
            for escala in escalas
        ],
        "miembros": miembros,
    })


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
    if tipo in {choice for choice, _label in Promocion.TIPO_BENEFICIO_CHOICES}:
        params["tipo"] = tipo

    return params


def _promociones_admin_queryset(request):
    filters = _promociones_admin_filter_params(request)
    queryset = (
        Promocion.objects.select_related("producto", "presentacion")
        .prefetch_related("escalas", "tipos_cliente", "productos_grupo", "productos_grupo__producto", "productos_grupo__presentacion")
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
            | Q(productos_grupo__producto__nombre__icontains=query)
            | Q(productos_grupo__producto__nombre_en__icontains=query)
        )

    if filters.get("producto"):
        queryset = queryset.filter(
            Q(producto_id=filters["producto"])
            | Q(productos_grupo__producto_id=filters["producto"])
        ).distinct()

    if filters.get("tipo"):
        queryset = queryset.filter(escalas__tipo_beneficio=filters["tipo"])

    return queryset.distinct(), filters


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

    filter_producto_nombre = ""
    if filter_params.get("producto"):
        filter_producto_nombre = (
            Producto.objects.filter(id=filter_params["producto"])
            .values_list("nombre", flat=True)
            .first()
            or ""
        )

    return render(request, "admin/promociones.html", {
        "promociones": page_obj.object_list,
        "page_obj": page_obj,
        "filter_params": filter_params,
        "filter_q": filter_params.get("q", ""),
        "filter_estado": filter_params.get("estado", "activas"),
        "filter_producto": filter_params.get("producto", ""),
        "filter_producto_nombre": filter_producto_nombre,
        "filter_tipo": filter_params.get("tipo", ""),
        "active_count": active_count,
        "inactive_count": inactive_count,
        "tipos_beneficio": Promocion.TIPO_BENEFICIO_CHOICES,
        "producto_search_url": reverse("buscar_productos_promocion"),
    })


def _promocion_escalas_formset_context(request=None, instance=None):
    if request is not None and request.method == "POST":
        return PromocionEscalaFormSet(request.POST, instance=instance, prefix="escalas")
    return PromocionEscalaFormSet(instance=instance, prefix="escalas")


def _promocion_productos_formset_context(request=None, instance=None):
    if request is not None and request.method == "POST":
        return PromocionProductoFormSet(request.POST, instance=instance, prefix="productos")
    return PromocionProductoFormSet(instance=instance, prefix="productos")


def _validar_promocion_grupo(productos_formset):
    activos = [
        form.cleaned_data
        for form in productos_formset.forms
        if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("producto")
    ]
    if len(activos) < 2:
        productos_formset._non_form_errors = productos_formset.error_class([
            _("Add at least two products for a combo promotion."),
        ])
        return False
    return True


def _sincronizar_promocion_producto_representativo(promocion):
    if promocion.alcance == Promocion.ALCANCE_GRUPO:
        primer = promocion.productos_grupo.select_related("producto", "presentacion").order_by("id").first()
        if primer is not None:
            promocion.producto_id = primer.producto_id
            promocion.presentacion_id = primer.presentacion_id
            promocion.save(update_fields=["producto", "presentacion", "actualizada_en"])
        return
    if promocion.producto_id:
        promocion.productos_grupo.exclude(producto_id=promocion.producto_id).delete()
        PromocionProducto.objects.update_or_create(
            promocion_id=promocion.id,
            producto_id=promocion.producto_id,
            defaults={"presentacion_id": promocion.presentacion_id},
        )


def _promocion_form_render_context(request, form, formset, productos_formset, promocion=None):
    producto_seleccionado = None
    presentacion_seleccionada = None
    productos_grupo = []
    if form.is_bound:
        producto_id = form.data.get("producto")
        if producto_id:
            producto_seleccionado = Producto.objects.filter(id=producto_id).first()
        presentacion_id = form.data.get("presentacion")
        if presentacion_id:
            presentacion_seleccionada = Presentacion.objects.filter(id=presentacion_id).first()
    elif promocion is not None:
        producto_seleccionado = promocion.producto
        presentacion_seleccionada = promocion.presentacion
        productos_grupo = list(
            promocion.productos_grupo.select_related("producto", "presentacion").order_by("id")
        )

    return {
        "promocion": promocion,
        "form": form,
        "escalas_formset": formset,
        "productos_formset": productos_formset,
        "productos_grupo": productos_grupo,
        "tipos_beneficio": Promocion.TIPO_BENEFICIO_CHOICES,
        "alcance_individual": Promocion.ALCANCE_INDIVIDUAL,
        "alcance_grupo": Promocion.ALCANCE_GRUPO,
        "percentage_preset_options": opciones_porcentaje_promocion(),
        "fixed_preset_options": opciones_monto_fijo_promocion(),
        "producto_seleccionado": producto_seleccionado,
        "presentacion_seleccionada": presentacion_seleccionada,
        "producto_search_url": reverse("buscar_productos_promocion"),
        "producto_presentaciones_url_template": reverse("producto_presentaciones_promocion", args=[0]).replace("/0/", "/__ID__/"),
    }


@login_required
@internal_permission_required("admin.products.manage")
def crear_promocion(request):
    if request.method == "POST":
        form = PromocionForm(request.POST, request.FILES)
        formset = _promocion_escalas_formset_context(request, instance=form.instance)
        productos_formset = _promocion_productos_formset_context(request, instance=form.instance)
        productos_valid = True
        if form.is_valid() and form.cleaned_data.get("alcance") == Promocion.ALCANCE_GRUPO:
            productos_valid = productos_formset.is_valid() and _validar_promocion_grupo(productos_formset)
        if form.is_valid() and formset.is_valid() and productos_valid:
            promocion = form.save()
            formset.instance = promocion
            formset.save()
            if promocion.alcance == Promocion.ALCANCE_GRUPO:
                productos_formset.instance = promocion
                productos_formset.save()
            _sincronizar_promocion_producto_representativo(promocion)
            messages.success(request, _("Promotion created successfully."))
            return redirect("lista_promociones")
        messages.error(request, _("Please fix the errors below."))
    else:
        form = PromocionForm()
        formset = _promocion_escalas_formset_context(instance=None)
        productos_formset = _promocion_productos_formset_context(instance=None)
    return render(
        request,
        "admin/promocion_form.html",
        _promocion_form_render_context(request, form, formset, productos_formset),
    )


@login_required
@internal_permission_required("admin.products.manage")
def editar_promocion(request, promocion_id):
    promocion = get_object_or_404(Promocion, id=promocion_id)
    if request.method == "POST":
        form = PromocionForm(request.POST, request.FILES, instance=promocion)
        formset = _promocion_escalas_formset_context(request, instance=promocion)
        productos_formset = _promocion_productos_formset_context(request, instance=promocion)
        productos_valid = True
        if form.is_valid() and form.cleaned_data.get("alcance") == Promocion.ALCANCE_GRUPO:
            productos_valid = productos_formset.is_valid() and _validar_promocion_grupo(productos_formset)
        if form.is_valid() and formset.is_valid() and productos_valid:
            clear_image = request.POST.get("imagen-clear") == "on"
            if clear_image and promocion.imagen:
                promocion.imagen.delete(save=False)
            promocion = form.save()
            formset.save()
            if promocion.alcance == Promocion.ALCANCE_GRUPO:
                productos_formset.save()
            _sincronizar_promocion_producto_representativo(promocion)
            messages.success(request, _("Promotion updated successfully."))
            return redirect("lista_promociones")
        messages.error(request, _("Please fix the errors below."))
    else:
        form = PromocionForm(instance=promocion)
        formset = _promocion_escalas_formset_context(instance=promocion)
        productos_formset = _promocion_productos_formset_context(instance=promocion)
    return render(
        request,
        "admin/promocion_form.html",
        _promocion_form_render_context(request, form, formset, productos_formset, promocion=promocion),
    )


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


