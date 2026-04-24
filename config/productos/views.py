from django.core.cache import cache
from django.db.models import Prefetch
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from .models import Producto, Categoria, Marca, Presentacion, ConfiguracionPrecios
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.utils.translation import gettext as _
from django.core.exceptions import ValidationError
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion
from config.usuarios.permissions import internal_permission_required


CATALOGO_CACHE_TIMEOUT = 60


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


def _hydrate_productos(productos):
    for producto in productos:
        presentaciones = list(producto.presentaciones.all())
        producto.presentaciones_prefetch = presentaciones
        producto.primera_presentacion = presentaciones[0] if presentaciones else None
    return productos


def _get_cached_catalogo_productos():
    cache_key = "catalogo:productos_activos"
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
            ).prefetch_related(_presentaciones_prefetch())
        ))
        cache.set(cache_key, productos, CATALOGO_CACHE_TIMEOUT)
    return productos


def _get_cached_catalogo_categorias():
    cache_key = "catalogo:categorias"
    categorias = cache.get(cache_key)
    if categorias is None:
        categorias = list(Categoria.objects.only("id", "nombre", "nombre_en"))
        cache.set(cache_key, categorias, CATALOGO_CACHE_TIMEOUT)
    return categorias


def _get_cached_catalogo_marcas():
    cache_key = "catalogo:marcas_activas"
    marcas = cache.get(cache_key)
    if marcas is None:
        marcas = list(
            Marca.objects.filter(activo=True).only(
                "id",
                "nombre",
                "nombre_en",
                "activo",
                "logo",
            ).prefetch_related("categorias")
        )
        for marca in marcas:
            marca.categorias_ids = " ".join(str(categoria.id) for categoria in marca.categorias.all())
        cache.set(cache_key, marcas, CATALOGO_CACHE_TIMEOUT)
    return marcas


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

    productos = _get_cached_catalogo_productos()
    categorias = _get_cached_catalogo_categorias()

    marcas = _get_cached_catalogo_marcas()

    context = {
        'productos': productos,
        'categorias': categorias,
        'marcas': marcas,
        'guest_mode': force_guest_mode,
        'can_quote': can_quote,
        'can_view_received_quotes': can_view_received_quotes,
        'pendientes_cotizaciones': pendientes_cotizaciones,
        'catalogo_url': catalogo_url,
        'client_price_tier': client_price_tier,
        'show_client_prices': client_price_tier is not None,
    }

    response = render(request, 'productos/catalogo.html', context)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response

@login_required
@internal_permission_required('admin.products.view')
def lista_productos(request):

    productos = Producto.objects.all()
    categorias = Categoria.objects.all()
    marcas = Marca.objects.all().prefetch_related('categorias')

    return render(request, 'admin/productos.html', {
        'productos': productos,
        'categorias': categorias,
        'marcas': marcas,
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

        nombre = request.POST.get("nombre")
        nombre_en = request.POST.get("nombre_en")
        codigo_barras = request.POST.get("codigo_barras")
        descripcion = request.POST.get("descripcion")
        descripcion_en = request.POST.get("descripcion_en")

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

        # Guardar la presentacion inicial si el formulario trae datos.
        presentacion_nombre = (request.POST.get("presentacion") or "").strip()
        tipo_contenido = (request.POST.get("tipo_contenido") or "unidades").strip() or "unidades"
        unidades_raw = (request.POST.get("unidades") or "").strip()

        costo_raw = (request.POST.get("costo") or "").strip()

        has_presentacion_data = any([
            presentacion_nombre,
            tipo_contenido,
            unidades_raw,
            costo_raw,
        ])

        if has_presentacion_data:
            try:
                unidades = int(unidades_raw) if unidades_raw else 1
            except ValueError:
                unidades = 1

            if unidades < 1:
                unidades = 1

            Presentacion.objects.create(
                producto=producto,
                nombre=presentacion_nombre or "Presentacion 1",
                tipo_contenido=tipo_contenido,
                unidades=unidades,
                costo=_parse_optional_decimal(costo_raw),
            )

        return redirect("lista_productos")

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
        messages.success(request, _("Price percentages updated successfully"))
        return redirect("configurar_precios")

    return render(request, "admin/configurar_precios.html", {
        "configuracion": configuracion,
        "price_margins": price_margins,
    })


@login_required
@internal_permission_required('admin.products.manage')
def crear_categoria(request):

    if request.method == "POST":
        nombre = (request.POST.get("nombre") or "").strip()
        nombre_en = (request.POST.get("nombre_en") or "").strip()

        if not nombre:
            return render(request, "admin/crear_categoria.html", {
                "error": _("El nombre de la categoria es obligatorio."),
                "nombre": nombre,
                "nombre_en": nombre_en,
            })

        if Categoria.objects.filter(nombre__iexact=nombre).exists():
            return render(request, "admin/crear_categoria.html", {
                "error": _("Ya existe una categoria con ese nombre."),
                "nombre": nombre,
                "nombre_en": nombre_en,
            })

        Categoria.objects.create(
            nombre=nombre,
            nombre_en=nombre_en,
        )
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

        if not nombre:
            return render(request, "admin/crear_marca.html", {
                "error": _("El nombre de la marca es obligatorio."),
                "categorias": categorias,
                "selected_categorias": [str(cid) for cid in categorias_ids],
                "nombre": nombre,
                "nombre_en": nombre_en,
            })

        if Marca.objects.filter(nombre__iexact=nombre).exists():
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

    presentaciones = producto.presentaciones.all()

    if request.method == "POST":

        # -------- PRODUCTO --------

        producto.nombre = request.POST.get("nombre")
        producto.nombre_en = request.POST.get("nombre_en")
        producto.codigo_barras = request.POST.get("codigo_barras")
        producto.descripcion = request.POST.get("descripcion")
        producto.descripcion_en = request.POST.get("descripcion_en")

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

        producto.save()

        # -------- PRESENTACIONES --------

        for p in presentaciones:

            p.nombre = request.POST.get(f"presentacion_nombre_{p.id}")
            p.tipo_contenido = request.POST.get(f"tipo_contenido_{p.id}")

            unidades = request.POST.get(f"unidades_{p.id}")
            if unidades:
                p.unidades = int(unidades)

            if f"costo_{p.id}" in request.POST:
                p.costo = _parse_optional_decimal(request.POST.get(f"costo_{p.id}"))

            p.save()

        # Si el producto no tenia presentaciones, permitir crear la primera desde esta vista.
        if not presentaciones.exists():
            nueva_nombre = (request.POST.get("presentacion_nueva") or "").strip()
            nuevo_tipo = (request.POST.get("tipo_contenido_nuevo") or "unidades").strip() or "unidades"
            nuevas_unidades_raw = (request.POST.get("unidades_nuevo") or "").strip()

            ncosto = (request.POST.get("costo_nuevo") or "").strip()

            if any([nueva_nombre, nuevas_unidades_raw, ncosto]):
                try:
                    nuevas_unidades = int(nuevas_unidades_raw) if nuevas_unidades_raw else 1
                except ValueError:
                    nuevas_unidades = 1

                if nuevas_unidades < 1:
                    nuevas_unidades = 1

                Presentacion.objects.create(
                    producto=producto,
                    nombre=nueva_nombre or "Presentacion 1",
                    tipo_contenido=nuevo_tipo,
                    unidades=nuevas_unidades,
                    costo=_parse_optional_decimal(ncosto),
                )

        return redirect("lista_productos")
    
    return render(request, "admin/editar_producto.html", {
        "producto": producto,
        "categorias": categorias,
        "marcas": marcas,
        "presentaciones": presentaciones,
        "price_margins": _get_price_margin_values(),
    })


