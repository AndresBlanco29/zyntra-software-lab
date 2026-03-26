from django.core.cache import cache
from django.db import OperationalError, ProgrammingError
from django.db.models import Prefetch
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
import logging
from config.productos.models import Producto, Marca, Presentacion
from .models import Testimonio, HomeContenido, ensure_homecontenido_quienes_schema
from urllib.parse import urlparse


HOME_CACHE_TIMEOUT = 60
logger = logging.getLogger(__name__)


def chunk(lista, n):
    """Divide la lista en grupos de n"""
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


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
        ).order_by("id"),
    )


def _hydrate_productos(productos):
    for producto in productos:
        presentaciones = list(producto.presentaciones.all())
        producto.presentaciones_prefetch = presentaciones
        producto.primera_presentacion = presentaciones[0] if presentaciones else None
    return productos


def _get_cached_home_productos():
    cache_key = "home:productos_destacados"
    productos = cache.get(cache_key)
    if productos is None:
        productos = _hydrate_productos(list(
            Producto.objects.filter(
                activo=True,
            ).filter(
                Q(destacado=True) | Q(descuento__gt=0)
            ).select_related("marca", "categoria").only(
                "id",
                "nombre",
                "nombre_en",
                "imagen",
                "descuento",
                "marca_id",
                "categoria_id",
            ).prefetch_related(_presentaciones_prefetch())
        ))
        cache.set(cache_key, productos, HOME_CACHE_TIMEOUT)
    return productos


def _get_cached_home_marcas():
    cache_key = "home:marcas_activas"
    marcas = cache.get(cache_key)
    if marcas is None:
        marcas = list(Marca.objects.filter(activo=True).only(
            "id",
            "nombre",
            "nombre_en",
            "logo",
            "activo",
        ))
        cache.set(cache_key, marcas, HOME_CACHE_TIMEOUT)
    return marcas


def _get_cached_home_testimonios():
    cache_key = "home:testimonios_activos"
    testimonios = cache.get(cache_key)
    if testimonios is None:
        testimonios = list(Testimonio.objects.filter(activo=True).only(
            "id",
            "nombre",
            "negocio",
            "negocio_en",
            "comentario",
            "comentario_en",
            "estrellas",
            "foto",
            "activo",
            "orden",
        ))
        cache.set(cache_key, testimonios, HOME_CACHE_TIMEOUT)
    return testimonios


def _get_cached_home_contenido():
    cache_key = "home:contenido"
    contenido = cache.get(cache_key)
    if contenido is None:
        try:
            contenido = HomeContenido.objects.filter(activo=True).only(
                "id",
                "hero_titulo_principal",
                "hero_titulo_principal_en",
                "hero_titulo_resaltado",
                "hero_titulo_resaltado_en",
                "hero_titulo_final",
                "hero_titulo_final_en",
                "hero_subtitulo",
                "hero_subtitulo_en",
                "hero_boton_texto",
                "hero_boton_texto_en",
                "cta_titulo",
                "cta_titulo_en",
                "cta_boton_registro_texto",
                "cta_boton_registro_texto_en",
                "cta_boton_catalogo_texto",
                "cta_boton_catalogo_texto_en",
                "quienes_titulo",
                "quienes_titulo_en",
                "quienes_descripcion",
                "quienes_descripcion_en",
                "beneficio_1_titulo",
                "beneficio_1_titulo_en",
                "beneficio_1_subtitulo",
                "beneficio_1_subtitulo_en",
                "beneficio_2_titulo",
                "beneficio_2_titulo_en",
                "beneficio_2_subtitulo",
                "beneficio_2_subtitulo_en",
                "beneficio_3_titulo",
                "beneficio_3_titulo_en",
                "beneficio_3_subtitulo",
                "beneficio_3_subtitulo_en",
                "beneficio_4_titulo",
                "beneficio_4_titulo_en",
                "beneficio_4_subtitulo",
                "beneficio_4_subtitulo_en",
                "estadistica_1_valor",
                "estadistica_1_valor_en",
                "estadistica_1_label",
                "estadistica_1_label_en",
                "estadistica_2_valor",
                "estadistica_2_valor_en",
                "estadistica_2_label",
                "estadistica_2_label_en",
                "estadistica_3_valor",
                "estadistica_3_valor_en",
                "estadistica_3_label",
                "estadistica_3_label_en",
                "activo",
            ).first()
        except (OperationalError, ProgrammingError):
            try:
                ensure_homecontenido_quienes_schema()
                contenido = HomeContenido.objects.filter(activo=True).only(
                    "id",
                    "hero_titulo_principal",
                    "hero_titulo_principal_en",
                    "hero_titulo_resaltado",
                    "hero_titulo_resaltado_en",
                    "hero_titulo_final",
                    "hero_titulo_final_en",
                    "hero_subtitulo",
                    "hero_subtitulo_en",
                    "hero_boton_texto",
                    "hero_boton_texto_en",
                    "cta_titulo",
                    "cta_titulo_en",
                    "cta_boton_registro_texto",
                    "cta_boton_registro_texto_en",
                    "cta_boton_catalogo_texto",
                    "cta_boton_catalogo_texto_en",
                    "quienes_titulo",
                    "quienes_titulo_en",
                    "quienes_descripcion",
                    "quienes_descripcion_en",
                    "beneficio_1_titulo",
                    "beneficio_1_titulo_en",
                    "beneficio_1_subtitulo",
                    "beneficio_1_subtitulo_en",
                    "beneficio_2_titulo",
                    "beneficio_2_titulo_en",
                    "beneficio_2_subtitulo",
                    "beneficio_2_subtitulo_en",
                    "beneficio_3_titulo",
                    "beneficio_3_titulo_en",
                    "beneficio_3_subtitulo",
                    "beneficio_3_subtitulo_en",
                    "beneficio_4_titulo",
                    "beneficio_4_titulo_en",
                    "beneficio_4_subtitulo",
                    "beneficio_4_subtitulo_en",
                    "estadistica_1_valor",
                    "estadistica_1_valor_en",
                    "estadistica_1_label",
                    "estadistica_1_label_en",
                    "estadistica_2_valor",
                    "estadistica_2_valor_en",
                    "estadistica_2_label",
                    "estadistica_2_label_en",
                    "estadistica_3_valor",
                    "estadistica_3_valor_en",
                    "estadistica_3_label",
                    "estadistica_3_label_en",
                    "activo",
                ).first()
            except Exception:
                contenido = None
        cache.set(cache_key, contenido, HOME_CACHE_TIMEOUT)
    return contenido


def _came_from_internal_route(request):
    referer = request.META.get("HTTP_REFERER", "")
    if not referer:
        return False

    try:
        parsed = urlparse(referer)
    except Exception:
        return False

    if parsed.netloc != request.get_host():
        return False

    current_path = (request.path or "/").rstrip("/") or "/"
    ref_path = (parsed.path or "/").rstrip("/") or "/"
    return ref_path != current_path


def home(request):
    try:
        marcas = _get_cached_home_marcas()
        testimonios = _get_cached_home_testimonios()
        productos_destacados = _get_cached_home_productos()
        ofertas_chunks = list(chunk(productos_destacados, 4))
        home_contenido = _get_cached_home_contenido()
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("home fallback due to db error: %s", exc)
        marcas = []
        testimonios = []
        productos_destacados = []
        ofertas_chunks = []
        home_contenido = None

    context = {
        "marcas": marcas,
        "testimonios": testimonios,
        "productos_destacados": productos_destacados,
        "ofertas_chunks": ofertas_chunks,
        "home_contenido": home_contenido,
    }
    return render(request, "home.html", context)


def health(request):
    return JsonResponse({"status": "ok"})