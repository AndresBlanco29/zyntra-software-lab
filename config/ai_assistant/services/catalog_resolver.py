import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse

from config.ai_assistant.models import AssistantProductAlias
from config.productos.models import Producto
from config.productos.promotions import promociones_activas_queryset


def normalize_catalog_term(value):
    text = unicodedata.normalize('NFKD', str(value or '').lower())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def _score(query, *values):
    query = normalize_catalog_term(query)
    best = 0.0
    for value in values:
        candidate = normalize_catalog_term(value)
        if not candidate:
            continue
        if candidate == query:
            best = max(best, 1.0)
        elif query in candidate:
            best = max(best, 0.9 + min(len(query) / max(len(candidate), 1), 0.09))
        else:
            best = max(best, SequenceMatcher(None, query, candidate).ratio())
    return best


def _promotion_product_ids(cliente):
    promotions = promociones_activas_queryset(cliente=cliente) if cliente else promociones_activas_queryset()
    ids = set(promotions.exclude(producto__isnull=True).values_list('producto_id', flat=True))
    ids.update(promotions.exclude(presentacion__isnull=True).values_list('presentacion__producto_id', flat=True))
    return ids


def _product_dto(product, query, promotion_ids):
    presentations = list(product.presentaciones.all())
    catalog_url = f"{reverse('catalogo')}?{urlencode({'q': query})}"
    return {
        'product_id': product.id,
        'name': product.nombre,
        'brand': product.marca.nombre if product.marca_id else '',
        'category': product.categoria.nombre if product.categoria_id else '',
        'catalog_url': catalog_url,
        'has_active_promotion': product.id in promotion_ids,
        'presentations': [
            {
                'id': presentation.id,
                'name': presentation.nombre_empaque_cliente,
                'units': presentation.unidades,
                'unit_type': presentation.tipo_contenido,
            }
            for presentation in presentations
        ],
        'primary_presentation_id': presentations[0].id if presentations else None,
    }


def find_products(query, *, cliente=None, limit=5):
    """Find catalog products from canonical data, aliases and bounded fuzzy ranking."""
    query = str(query or '').strip()
    normalized = normalize_catalog_term(query)
    if not normalized:
        return {'query': query, 'products': [], 'related_products': []}

    aliases = list(
        AssistantProductAlias.objects.filter(active=True, alias__icontains=query)
        .select_related('product', 'brand')[:20]
    )
    alias_product_ids = [item.product_id for item in aliases if item.product_id]
    alias_brand_ids = [item.brand_id for item in aliases if item.brand_id]
    terms = [query] + [item.alias for item in aliases]
    predicate = Q(pk__in=alias_product_ids) | Q(marca_id__in=alias_brand_ids)
    for term in terms:
        predicate |= (
            Q(nombre__icontains=term)
            | Q(nombre_en__icontains=term)
            | Q(codigo_barras__icontains=term)
            | Q(marca__nombre__icontains=term)
            | Q(marca__nombre_en__icontains=term)
            | Q(categoria__nombre__icontains=term)
            | Q(categoria__nombre_en__icontains=term)
            | Q(presentaciones__nombre__icontains=term)
            | Q(presentaciones__nombre_en__icontains=term)
        )
    candidates = list(
        Producto.objects.filter(activo=True).filter(predicate).distinct()
        .select_related('marca', 'categoria')
        .prefetch_related('presentaciones')[:60]
    )
    # If substring matching produced no candidates, rank a bounded active catalog set.
    if not candidates:
        candidates = list(
            Producto.objects.filter(activo=True).select_related('marca', 'categoria').prefetch_related('presentaciones')[:400]
        )
    ranked = sorted(
        (
            (
                product,
                _score(
                    normalized,
                    product.nombre,
                    product.nombre_en,
                    product.marca.nombre if product.marca_id else '',
                    product.categoria.nombre if product.categoria_id else '',
                    *(presentation.nombre for presentation in product.presentaciones.all()),
                ),
            )
            for product in candidates
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    selected = [product for product, score in ranked if score >= 0.43][:limit]
    promotion_ids = _promotion_product_ids(cliente)
    products = [_product_dto(product, query, promotion_ids) for product in selected]
    related = []
    if selected:
        anchor = selected[0]
        relation = Q()
        if anchor.marca_id:
            relation |= Q(marca_id=anchor.marca_id)
        if anchor.categoria_id:
            relation |= Q(categoria_id=anchor.categoria_id)
        related_queryset = (
            Producto.objects.filter(activo=True).filter(relation).exclude(pk=anchor.pk).distinct()
            .select_related('marca', 'categoria').prefetch_related('presentaciones')[:4]
        )
        related = [_product_dto(product, product.nombre, promotion_ids) for product in related_queryset]
    return {'query': query, 'products': products, 'related_products': related}
