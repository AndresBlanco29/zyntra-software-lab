from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse

from config.productos.promotions import promociones_activas_queryset


def active_promotion_cards(*, cliente=None, related_product_id=None, limit=6):
    """Return safe, current promotion DTOs for chat and Function Calling."""
    promotions = promociones_activas_queryset(cliente=cliente).prefetch_related(
        'escalas',
        'productos_grupo__producto',
        'productos_grupo__presentacion',
    )
    related = False
    if related_product_id:
        matching = promotions.filter(
            Q(producto_id=related_product_id) | Q(productos_grupo__producto_id=related_product_id)
        ).distinct()
        if matching.exists():
            promotions = matching
            related = True
    cards = []
    for promotion in promotions[:limit]:
        group_item = next(iter(promotion.productos_grupo.all()), None)
        product = promotion.producto or (group_item.producto if group_item else None)
        presentation = promotion.presentacion or (group_item.presentacion if group_item else None)
        if product is None:
            continue
        benefits = [
            f'Compra {scale.cantidad_minima}+ y recibe {scale.texto_beneficio()}'
            for scale in promotion.escalas.all()[:2]
        ]
        cards.append({
            'promotion_id': promotion.id,
            'product_id': product.id,
            'product_name': product.nombre,
            'presentation': presentation.nombre_empaque_cliente if presentation else '',
            'promotion_name': promotion.nombre,
            'description': promotion.descripcion,
            'benefits': benefits,
            'expires_at': promotion.fecha_fin.isoformat() if promotion.fecha_fin else None,
            'catalog_url': f'{reverse("catalogo")}?{urlencode({"promociones": 1, "q": product.nombre})}',
        })
    return {'related': related, 'cards': cards}
