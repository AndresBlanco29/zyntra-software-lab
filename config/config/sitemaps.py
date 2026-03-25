from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from config.productos.models import Producto


class StaticViewSitemap(Sitemap):
    priority = 1.0
    changefreq = "weekly"

    def items(self):
        return ["home"]

    def location(self, item):
        return reverse(item)


class CatalogViewSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.8

    def items(self):
        return ["catalogo"]

    def location(self, item):
        return reverse(item)

    def lastmod(self, _item):
        return Producto.objects.filter(activo=True).order_by("-creado_en").values_list("creado_en", flat=True).first()


sitemaps = {
    "static": StaticViewSitemap,
    "catalogo": CatalogViewSitemap,
}
