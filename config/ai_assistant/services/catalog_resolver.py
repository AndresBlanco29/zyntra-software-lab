import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse

from config.ai_assistant.models import AssistantProductAlias
from config.productos.models import Producto
from config.productos.promotions import promociones_activas_queryset

RELATED_TERMS = {
    'coke': 'coca cola',
    'coca': 'coca cola',
    'coca cola': 'coca cola',
    '3 litros': '3lt',
    '3 litro': '3lt',
    'litros': 'lt',
    'litro': 'lt',
}

MAX_CATALOG_RESULTS = 50
FUZZY_TOKEN_THRESHOLD = 0.78


PACKAGING_WORDS = (
    'cajas', 'caja', 'unidades', 'unidad', 'paquetes', 'paquete', 'bultos', 'bulto',
    'piezas', 'pieza', 'cases', 'case', 'boxes', 'box', 'packs', 'pack', 'cs', 'und', 'pza',
)
SIZE_WORDS = ('lt', 'l', 'oz', 'ml', 'litros', 'litro', 'gr', 'g', 'kg', 'lb', 'lbs', 'gal')


def normalize_catalog_term(value):
    text = unicodedata.normalize('NFKD', str(value or '').lower())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def _compact_catalog_term(value):
    """Canonical comparison form that also ignores spaces and punctuation."""
    return normalize_catalog_term(value).replace(' ', '')


def strip_quantity_noise(value):
    """Drop the "10 cajas de" wording so only the catalog term is searched.

    Customers type the amount and the product together. Leaving "10 cajas" in the
    query diluted the token overlap enough to push a real product below the match
    threshold, so the catalog answered that it did not exist.
    """
    text = normalize_catalog_term(value)
    packaging = '|'.join(PACKAGING_WORDS)
    text = re.sub(rf'\b\d+\s*(?:{packaging})\b', ' ', text)
    # A leading amount, unless it is really a size such as "3 litros".
    text = re.sub(rf'^\s*\d{{1,4}}\s+(?!(?:{"|".join(SIZE_WORDS)})\b)', ' ', text)
    text = re.sub(rf'\b(?:{packaging})\b', ' ', text)
    cleaned = re.sub(r'\s+', ' ', text).strip()
    return cleaned or normalize_catalog_term(value)


def _stem(token):
    """Fold Spanish plurals so "jarritos" still matches the catalog's "JARRITO"."""
    if len(token) > 4 and token.endswith('es') and not token.endswith('ses'):
        return token[:-2]
    if len(token) > 3 and token.endswith('s'):
        return token[:-1]
    return token


def _score(query, *values):
    query = normalize_catalog_term(query)
    compact_query = _compact_catalog_term(query)
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
        # "jarri-tos", "jarri tos" and "jarritos" refer to the same family.
        compact_candidate = _compact_catalog_term(candidate)
        if compact_query and compact_candidate:
            best = max(best, SequenceMatcher(None, compact_query, compact_candidate).ratio())
    return best


def _query_tokens(value):
    normalized = normalize_catalog_term(value)
    for source, replacement in RELATED_TERMS.items():
        if source in normalized:
            normalized = normalized.replace(source, replacement)
    return {_stem(token) for token in normalized.split() if len(token) > 1}


def catalog_tokens(value):
    """Normalized, plural-folded words of a phrase, for comparing against a name."""
    return _query_tokens(value)


def _fuzzy_token_overlap(query_tokens, candidate_tokens):
    """Return a tolerant token match score for small customer spelling mistakes."""
    if not query_tokens or not candidate_tokens:
        return 0.0
    matches = []
    for query_token in query_tokens:
        best = max(
            (SequenceMatcher(None, query_token, candidate_token).ratio()
             for candidate_token in candidate_tokens),
            default=0.0,
        )
        matches.append(best if best >= FUZZY_TOKEN_THRESHOLD else 0.0)
    return sum(matches) / len(query_tokens)


def _size_tokens(value):
    normalized = normalize_catalog_term(value).replace('litros', 'lt').replace('litro', 'lt')
    return set(re.findall(r'\b\d+\s*(?:lt|l|oz|ml)\b', normalized)) | set(re.findall(r'\b\d+\s+\d+(?:lt|l|oz|ml)\b', normalized))


def _product_score(query, product):
    names = [product.nombre, product.nombre_en, product.codigo_barras or '']
    if product.marca_id:
        names.extend([product.marca.nombre, product.marca.nombre_en])
    names.extend(presentation.nombre for presentation in product.presentaciones.all())
    normalized_query = normalize_catalog_term(query)
    compact_query = _compact_catalog_term(query)
    compact_query_forms = {compact_query, _stem(compact_query)}
    query_tokens = _query_tokens(query)
    candidate_tokens = set().union(*(_query_tokens(name) for name in names if name))
    token_overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens), 1)
    fuzzy_token_overlap = _fuzzy_token_overlap(query_tokens, candidate_tokens)
    compact_match = any(
        any(form and form in _compact_catalog_term(name) for form in compact_query_forms)
        for name in names if name
    )
    token_match = 1.0 if compact_match else max(token_overlap, fuzzy_token_overlap)
    fuzzy = _score(normalized_query, *names)
    query_sizes = _size_tokens(query)
    candidate_sizes = set().union(*(_size_tokens(name) for name in names if name))
    size_bonus = 0.22 if query_sizes and query_sizes & candidate_sizes else 0
    exact_bonus = 0.35 if normalize_catalog_term(product.nombre) == normalized_query else 0
    contains_bonus = 0.18 if normalized_query in normalize_catalog_term(product.nombre) else 0
    score = min(1.0, (token_match * 0.58) + (fuzzy * 0.25) + size_bonus + exact_bonus + contains_bonus)
    return score, {
        'token_overlap': round(token_overlap, 3),
        'fuzzy_token_overlap': round(fuzzy_token_overlap, 3),
        'compact_match': compact_match,
        'fuzzy_score': round(fuzzy, 3),
        'size_match': bool(query_sizes and query_sizes & candidate_sizes),
    }


def _promotion_product_ids(cliente):
    promotions = promociones_activas_queryset(cliente=cliente) if cliente else promociones_activas_queryset()
    ids = set(promotions.exclude(producto__isnull=True).values_list('producto_id', flat=True))
    ids.update(promotions.exclude(presentacion__isnull=True).values_list('presentacion__producto_id', flat=True))
    return ids


def _product_dto(product, query, promotion_ids, cliente, score=0, match=None):
    presentations = list(product.presentaciones.all())
    catalog_url = f"{reverse('catalogo')}?{urlencode({'q': query})}"
    price_tier = cliente.get_nivel_precio_normalizado() if cliente and cliente.has_assigned_price_tier() else None
    return {
        'product_id': product.id,
        'name': product.nombre,
        'brand': product.marca.nombre if product.marca_id else '',
        'category': product.categoria.nombre if product.categoria_id else '',
        'catalog_url': catalog_url,
        'has_active_promotion': product.id in promotion_ids,
        'pricing_available': bool(price_tier),
        'presentations': [
            {
                'id': presentation.id,
                'name': presentation.nombre_empaque_cliente,
                'units': presentation.unidades,
                'unit_type': presentation.tipo_contenido,
                'price': (
                    str(getattr(presentation, f'precio_{price_tier}'))
                    if price_tier and getattr(presentation, f'precio_{price_tier}') is not None
                    else None
                ),
            }
            for presentation in presentations
        ],
        'primary_presentation_id': presentations[0].id if presentations else None,
        'score': round(score, 3),
        'match': match or {},
    }


def _term_predicate(term):
    return (
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


def _candidates_for(predicate, limit=500):
    return list(
        Producto.objects.filter(activo=True).filter(predicate).distinct()
        .select_related('marca', 'categoria')
        .prefetch_related('presentaciones')[:limit]
    )


def find_products(query, *, cliente=None, limit=MAX_CATALOG_RESULTS):
    """Find catalog products, retaining every confirmed variant in the request family."""
    query = str(query or '').strip()
    if not normalize_catalog_term(query):
        return {'query': query, 'products': [], 'related_products': []}
    # Everything downstream ranks and links against the cleaned term, so the answer
    # and the catalog URL reflect what was actually searched.
    search_query = strip_quantity_noise(query)

    aliases = list(
        AssistantProductAlias.objects.filter(active=True, alias__icontains=search_query)
        .select_related('product', 'brand')[:20]
    )
    alias_product_ids = [item.product_id for item in aliases if item.product_id]
    alias_brand_ids = [item.brand_id for item in aliases if item.brand_id]
    predicate = Q(pk__in=alias_product_ids) | Q(marca_id__in=alias_brand_ids)
    for term in [search_query] + [item.alias for item in aliases]:
        predicate |= _term_predicate(term)
    candidates = _candidates_for(predicate)

    # The whole phrase rarely appears verbatim in a name. Retry word by word on the
    # singular stem before giving up, so a plural or a reordered phrase still hits.
    if not candidates:
        token_predicate = Q()
        for token in sorted(_query_tokens(search_query)):
            if len(token) >= 4:
                token_predicate |= _term_predicate(token)
                # A four-character prefix obtains the right catalog family even
                # when a customer adds, drops, hyphenates or separates letters.
                token_predicate |= _term_predicate(token[:4])
        if token_predicate:
            candidates = _candidates_for(token_predicate)

    # Last resort: rank active catalog products using typo-tolerant token matching.
    if not candidates:
        candidates = list(
            Producto.objects.filter(activo=True).select_related('marca', 'categoria').prefetch_related('presentaciones')[:500]
        )
    ranked = sorted(
        (
            (
                product,
                _product_score(search_query, product),
            )
            for product in candidates
        ),
        key=lambda item: item[1][0],
        reverse=True,
    )
    selected = [(product, score, match) for product, (score, match) in ranked if score >= 0.55][:limit]
    promotion_ids = _promotion_product_ids(cliente)
    products = [
        _product_dto(product, search_query, promotion_ids, cliente, score=score, match=match)
        for product, score, match in selected
    ]
    related = []
    if selected:
        anchor = selected[0][0]
        relation = Q()
        if anchor.marca_id:
            relation |= Q(marca_id=anchor.marca_id)
        if anchor.categoria_id:
            relation |= Q(categoria_id=anchor.categoria_id)
        related_queryset = (
            Producto.objects.filter(activo=True).filter(relation).exclude(pk=anchor.pk).distinct()
            .select_related('marca', 'categoria').prefetch_related('presentaciones')[:4]
        )
        related = [_product_dto(product, product.nombre, promotion_ids, cliente) for product in related_queryset]
    return {'query': search_query, 'products': products, 'related_products': related}
