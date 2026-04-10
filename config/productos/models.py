import unicodedata
from decimal import Decimal, ROUND_HALF_UP

from django.db import models
from django.utils.translation import get_language, gettext_lazy as _


def _normalize_translation_term(value):
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


PRESENTACION_TERM_TRANSLATIONS = {
    "unidad": {"es": "unidad", "en": "unit"},
    "unidades": {"es": "unidades", "en": "units"},
    "unit": {"es": "unidad", "en": "unit"},
    "units": {"es": "unidades", "en": "units"},
    "caja": {"es": "caja", "en": "box"},
    "cajas": {"es": "cajas", "en": "boxes"},
    "box": {"es": "caja", "en": "box"},
    "boxes": {"es": "cajas", "en": "boxes"},
    "paquete": {"es": "paquete", "en": "package"},
    "paquetes": {"es": "paquetes", "en": "packages"},
    "package": {"es": "paquete", "en": "package"},
    "packages": {"es": "paquetes", "en": "packages"},
    "pack": {"es": "pack", "en": "pack"},
    "packs": {"es": "packs", "en": "packs"},
    "bolsa": {"es": "bolsa", "en": "bag"},
    "bolsas": {"es": "bolsas", "en": "bags"},
    "bag": {"es": "bolsa", "en": "bag"},
    "bags": {"es": "bolsas", "en": "bags"},
    "botella": {"es": "botella", "en": "bottle"},
    "botellas": {"es": "botellas", "en": "bottles"},
    "bottle": {"es": "botella", "en": "bottle"},
    "bottles": {"es": "botellas", "en": "bottles"},
    "lata": {"es": "lata", "en": "can"},
    "latas": {"es": "latas", "en": "cans"},
    "can": {"es": "lata", "en": "can"},
    "cans": {"es": "latas", "en": "cans"},
    "pallet": {"es": "pallet", "en": "pallet"},
    "pallets": {"es": "pallets", "en": "pallets"},
}


def _translate_presentacion_term(value, target_language):
    translation = PRESENTACION_TERM_TRANSLATIONS.get(_normalize_translation_term(value))
    if translation:
        return translation[target_language]
    return value


def _resolve_presentacion_translation(primary_value, secondary_value, target_language):
    for candidate in ((secondary_value, primary_value) if target_language == "en" else (primary_value, secondary_value)):
        if candidate:
            return _translate_presentacion_term(candidate, target_language)
    return ""


DEFAULT_PRICE_MARGIN_PERCENTAGES = (
    Decimal("10"),
    Decimal("20"),
    Decimal("30"),
    Decimal("40"),
    Decimal("50"),
)


def _quantize_money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calculate_price_from_margin(cost, margin_percentage):
    cost_decimal = Decimal(str(cost or 0))
    if cost_decimal <= 0:
        return Decimal("0.00")

    divisor = Decimal("1") - (Decimal(str(margin_percentage)) / Decimal("100"))
    if divisor <= 0:
        return Decimal("0.00")

    return _quantize_money(cost_decimal / divisor)

class Categoria(models.Model):

    nombre = models.CharField(max_length=100)
    nombre_en = models.CharField(max_length=100, blank=True)

    @property
    def nombre_traducido(self):
        if get_language() == "en" and self.nombre_en:
            return self.nombre_en
        return self.nombre

    def __str__(self):
        return self.nombre_traducido

class Marca(models.Model):

    nombre = models.CharField(max_length=100)
    nombre_en = models.CharField(max_length=100, blank=True)

    activo = models.BooleanField(default=True)

    logo = models.ImageField(upload_to="marcas/", blank=True, null=True)

    categorias = models.ManyToManyField(Categoria, blank=True)

    def nombre_traducido(self):
        if get_language() == "en" and self.nombre_en:
            return self.nombre_en
        return self.nombre

    def __str__(self):
        return self.nombre
    
class Producto(models.Model):

    nombre = models.CharField(max_length=255)
    nombre_en = models.CharField(max_length=255, blank=True)

    descripcion = models.TextField(blank=True, null=True)
    descripcion_en = models.TextField(blank=True, null=True)

    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    marca = models.ForeignKey(
        Marca,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    imagen = models.ImageField(
        upload_to='productos/',
        blank=True,
        null=True
    )

    codigo_barras = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        help_text="Código de barras único del producto"
    )

    activo = models.BooleanField(default=True)

    destacado = models.BooleanField(default=False)

    descuento = models.IntegerField(default=0)

    quickbooks_id = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    @property
    def nombre_traducido(self):
        if get_language() == "en" and self.nombre_en:
            return self.nombre_en
        return self.nombre

    def __str__(self):
        return self.nombre

class Presentacion(models.Model):

    producto = models.ForeignKey(
        'Producto',
        on_delete=models.CASCADE,
        related_name='presentaciones'
    )

    nombre = models.CharField(max_length=100)
    nombre_en = models.CharField(max_length=100, blank=True)

    unidades = models.IntegerField()

    tipo_contenido = models.CharField(max_length=50, default="unidades")
    tipo_contenido_en = models.CharField(max_length=50, blank=True)

    costo = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    precio_1 = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    precio_2 = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    precio_3 = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    precio_4 = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    precio_5 = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    @property
    def tipo_contenido_traducido(self):
        target_language = "en" if get_language().startswith("en") else "es"
        return _resolve_presentacion_translation(self.tipo_contenido, self.tipo_contenido_en, target_language)

    @property
    def nombre_traducido(self):
        target_language = "en" if get_language().startswith("en") else "es"
        return _resolve_presentacion_translation(self.nombre, self.nombre_en, target_language)

    def recalcular_precios(self):
        if self.costo is None:
            return

        porcentajes = ConfiguracionPrecios.obtener_porcentajes()

        precios = [
            _calculate_price_from_margin(self.costo, porcentaje)
            for porcentaje in porcentajes
        ]

        self.precio_1, self.precio_2, self.precio_3, self.precio_4, self.precio_5 = precios

    def save(self, *args, **kwargs):
        self.recalcular_precios()
        super().save(*args, **kwargs)


class ConfiguracionPrecios(models.Model):
    porcentaje_1 = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    porcentaje_2 = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    porcentaje_3 = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    porcentaje_4 = models.DecimalField(max_digits=5, decimal_places=2, default=40)
    porcentaje_5 = models.DecimalField(max_digits=5, decimal_places=2, default=50)

    class Meta:
        verbose_name = _("Price configuration")
        verbose_name_plural = _("Price configuration")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return

    @classmethod
    def obtener(cls):
        configuracion, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "porcentaje_1": DEFAULT_PRICE_MARGIN_PERCENTAGES[0],
                "porcentaje_2": DEFAULT_PRICE_MARGIN_PERCENTAGES[1],
                "porcentaje_3": DEFAULT_PRICE_MARGIN_PERCENTAGES[2],
                "porcentaje_4": DEFAULT_PRICE_MARGIN_PERCENTAGES[3],
                "porcentaje_5": DEFAULT_PRICE_MARGIN_PERCENTAGES[4],
            },
        )
        return configuracion

    @classmethod
    def obtener_porcentajes(cls):
        configuracion = cls.obtener()
        return (
            configuracion.porcentaje_1,
            configuracion.porcentaje_2,
            configuracion.porcentaje_3,
            configuracion.porcentaje_4,
            configuracion.porcentaje_5,
        )

    def porcentajes_lista(self):
        return list(self.obtener_porcentajes())

    def __str__(self):
        return _("Price configuration")