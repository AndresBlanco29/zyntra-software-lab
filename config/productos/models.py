import unicodedata
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import get_language, gettext_lazy as _

from config.integrations.quickbooks.constants import QUICKBOOKS_SYNC_STATUS_CHOICES, QUICKBOOKS_SYNC_STATUS_PENDING


PLACEHOLDER_CODIGO_BARRAS_VALUES = frozenset({
    '',
    'none',
    'null',
    'n/a',
    'na',
    '-',
    'undefined',
})


def normalize_codigo_barras(value):
    """Return a real barcode or None. Empty / placeholder text must not hit the unique index."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in PLACEHOLDER_CODIGO_BARRAS_VALUES:
        return None
    return text


def _normalize_translation_term(value):
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


PRESENTACION_TERM_TRANSLATIONS = {
    "unidad": {"es": "unidad", "en": "unit"},
    "unidades": {"es": "unidades", "en": "units"},
    "unit": {"es": "unidad", "en": "unit"},
    "units": {"es": "unidades", "en": "units"},
    "caja": {"es": "CS", "en": "CS"},
    "cajas": {"es": "CS", "en": "CS"},
    "box": {"es": "CS", "en": "CS"},
    "boxes": {"es": "CS", "en": "CS"},
    "case": {"es": "CS", "en": "CS"},
    "cases": {"es": "CS", "en": "CS"},
    "cs": {"es": "CS", "en": "CS"},
    "bx": {"es": "CS", "en": "CS"},
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

DEFAULT_CUSTOMER_PRICE_TIER = 1


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


def _validate_margin_percentage(value):
    percentage = Decimal(str(value or 0))
    if percentage < 0:
        raise ValidationError(_('Utility percentages must be zero or greater.'))
    if percentage >= 100:
        raise ValidationError(_('Utility percentages must be less than 100 because the formula is cost / (1 - percentage).'))
    return percentage.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_price_tier(value, default=None):
    try:
        tier = int(value)
    except (TypeError, ValueError):
        return default

    if 1 <= tier <= 5:
        return tier
    return default

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

    sync_status = models.CharField(
        max_length=20,
        choices=QUICKBOOKS_SYNC_STATUS_CHOICES,
        default=QUICKBOOKS_SYNC_STATUS_PENDING,
        db_index=True,
    )

    last_synced_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    @property
    def nombre_traducido(self):
        if get_language() == "en" and self.nombre_en:
            return self.nombre_en
        return self.nombre

    def save(self, *args, **kwargs):
        self.codigo_barras = normalize_codigo_barras(self.codigo_barras)
        if self.nombre_en is None:
            self.nombre_en = ''
        if self.descripcion_en is None:
            self.descripcion_en = ''
        super().save(*args, **kwargs)

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

    costo = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name=_('RCost'),
        help_text=_('Real cost from QuickBooks / catalog cost used as the base RCost.'),
    )

    LANDED_OVERRIDE_NONE = ''
    LANDED_OVERRIDE_PERCENT = 'PERCENT'
    LANDED_OVERRIDE_FIXED = 'FIXED'
    LANDED_OVERRIDE_CHOICES = (
        (LANDED_OVERRIDE_NONE, _('Use global Landed Cost')),
        (LANDED_OVERRIDE_PERCENT, _('Percent override')),
        (LANDED_OVERRIDE_FIXED, _('Fixed $ override')),
    )

    landed_cost_override_tipo = models.CharField(
        max_length=20,
        choices=LANDED_OVERRIDE_CHOICES,
        blank=True,
        default=LANDED_OVERRIDE_NONE,
        verbose_name=_('Landed Cost override type'),
    )
    landed_cost_override_valor = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name=_('Landed Cost override value'),
        help_text=_('Percent or fixed dollars depending on the override type. Leave empty to use the global Landed Cost.'),
    )

    qb_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name=_('QB-PRICE'),
        help_text=_('Sales price imported from QuickBooks (Sales Price / UnitPrice).'),
    )

    peso_por_caja = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        blank=True,
        null=True,
        verbose_name=_('Weight per case (LB)'),
        help_text=_('Case weight in pounds used on invoices for Total WGT.'),
    )

    pallet_tie = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_('Pallet tie'),
        help_text=_('How many cases go on one pallet layer (bed).'),
    )
    pallet_high = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_('Pallet high'),
        help_text=_('How many layers go on one pallet.'),
    )
    pallet_quantity = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_('Pallet quantity'),
        help_text=_('Total cases per pallet (pallet tie × pallet high).'),
    )

    quickbooks_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    sync_status = models.CharField(
        max_length=20,
        choices=QUICKBOOKS_SYNC_STATUS_CHOICES,
        default=QUICKBOOKS_SYNC_STATUS_PENDING,
        db_index=True,
    )

    last_synced_at = models.DateTimeField(blank=True, null=True)

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

    @property
    def descripcion_empaque_cliente(self):
        from config.productos.packaging import get_effective_packaging_for_display

        return get_effective_packaging_for_display(self)['description']

    @property
    def nombre_empaque_cliente(self):
        from config.productos.packaging import get_effective_packaging_for_display

        return get_effective_packaging_for_display(self)['presentation_name']

    def recalcular_precios(self):
        if self.costo is None:
            return

        porcentajes = ConfiguracionPrecios.obtener_porcentajes()

        precios = [
            _calculate_price_from_margin(self.costo, porcentaje)
            for porcentaje in porcentajes
        ]

        self.precio_1, self.precio_2, self.precio_3, self.precio_4, self.precio_5 = precios

    def recalcular_pallet_quantity(self):
        if self.pallet_tie and self.pallet_high:
            self.pallet_quantity = int(self.pallet_tie) * int(self.pallet_high)
        else:
            self.pallet_quantity = None

    def get_price_for_tier(self, tier=None):
        normalized_tier = normalize_price_tier(tier, default=None)
        if normalized_tier is None:
            return None
        if self.costo is not None:
            porcentajes = ConfiguracionPrecios.obtener_porcentajes()
            return _calculate_price_from_margin(self.costo, porcentajes[normalized_tier - 1])
        return getattr(self, f'precio_{normalized_tier}', self.precio_1)

    @property
    def rcost(self):
        """Alias for the real QuickBooks / catalog unit cost."""
        return self.costo

    @property
    def landed_cost_amount(self):
        from config.productos.landed_cost import resolve_landed_cost_amount

        return resolve_landed_cost_amount(self)

    @property
    def effective_cost(self):
        from config.productos.landed_cost import resolve_effective_cost

        return resolve_effective_cost(self)

    def save(self, *args, **kwargs):
        self.recalcular_precios()
        self.recalcular_pallet_quantity()
        super().save(*args, **kwargs)


class ConfiguracionLandedCost(models.Model):
    TIPO_PERCENT = 'PERCENT'
    TIPO_FIXED = 'FIXED'
    TIPO_CHOICES = (
        (TIPO_PERCENT, _('Percent of RCost')),
        (TIPO_FIXED, _('Fixed dollars per unit')),
    )

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TIPO_PERCENT)
    valor = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        verbose_name = _('Landed Cost configuration')
        verbose_name_plural = _('Landed Cost configuration')

    def clean(self):
        amount = _quantize_money(self.valor or 0)
        if amount < 0:
            raise ValidationError({'valor': _('Landed Cost must be zero or greater.')})
        if self.tipo == self.TIPO_PERCENT and amount > Decimal('100'):
            raise ValidationError({'valor': _('Landed Cost percentage cannot exceed 100.')})
        self.valor = amount

    def save(self, *args, **kwargs):
        self.pk = 1
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return

    @classmethod
    def obtener(cls):
        configuracion, _ = cls.objects.get_or_create(
            pk=1,
            defaults={'tipo': cls.TIPO_PERCENT, 'valor': Decimal('0.00')},
        )
        return configuracion

    def __str__(self):
        return _('Landed Cost configuration')


class ConfiguracionPrecios(models.Model):
    porcentaje_1 = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    porcentaje_2 = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    porcentaje_3 = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    porcentaje_4 = models.DecimalField(max_digits=5, decimal_places=2, default=40)
    porcentaje_5 = models.DecimalField(max_digits=5, decimal_places=2, default=50)

    class Meta:
        verbose_name = _("Price configuration")
        verbose_name_plural = _("Price configuration")

    def clean(self):
        self.porcentaje_1 = _validate_margin_percentage(self.porcentaje_1)
        self.porcentaje_2 = _validate_margin_percentage(self.porcentaje_2)
        self.porcentaje_3 = _validate_margin_percentage(self.porcentaje_3)
        self.porcentaje_4 = _validate_margin_percentage(self.porcentaje_4)
        self.porcentaje_5 = _validate_margin_percentage(self.porcentaje_5)

    def save(self, *args, **kwargs):
        self.pk = 1
        self.full_clean()
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


DEFAULT_PRESET_DISCOUNT_AMOUNTS = (
    Decimal("0.25"),
    Decimal("0.50"),
    Decimal("0.75"),
    Decimal("1.00"),
    Decimal("1.50"),
    Decimal("2.00"),
    Decimal("2.50"),
    Decimal("3.00"),
    Decimal("4.00"),
    Decimal("5.00"),
)


def _validate_discount_preset_amount(value):
    amount = _quantize_money(value or 0)
    if amount < 0:
        raise ValidationError(_("Discount amounts must be zero or greater."))
    return amount


class ConfiguracionDescuentos(models.Model):
    descuento_1 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[0])
    descuento_2 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[1])
    descuento_3 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[2])
    descuento_4 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[3])
    descuento_5 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[4])
    descuento_6 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[5])
    descuento_7 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[6])
    descuento_8 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[7])
    descuento_9 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[8])
    descuento_10 = models.DecimalField(max_digits=10, decimal_places=2, default=DEFAULT_PRESET_DISCOUNT_AMOUNTS[9])

    class Meta:
        verbose_name = _("Discount configuration")
        verbose_name_plural = _("Discount configuration")

    def clean(self):
        for index in range(1, 11):
            field_name = f"descuento_{index}"
            setattr(self, field_name, _validate_discount_preset_amount(getattr(self, field_name)))

    def save(self, *args, **kwargs):
        self.pk = 1
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return

    @classmethod
    def obtener(cls):
        defaults = {f"descuento_{index}": amount for index, amount in enumerate(DEFAULT_PRESET_DISCOUNT_AMOUNTS, start=1)}
        configuracion, _ = cls.objects.get_or_create(pk=1, defaults=defaults)
        return configuracion

    def descuentos_lista(self):
        return [_quantize_money(getattr(self, f"descuento_{index}") or 0) for index in range(1, 11)]

    def opciones_activas(self):
        options = []
        for index, amount in enumerate(self.descuentos_lista(), start=1):
            if amount <= 0:
                continue
            options.append({
                "key": f"descuento_{index}",
                "value": format(amount, ".2f"),
                "label": str(_("Discount %(number)s - $%(amount)s") % {
                    "number": index,
                    "amount": format(amount, ".2f"),
                }),
            })
        return options

    def __str__(self):
        return _("Discount configuration")


class Promocion(models.Model):
    """
    Promotion "header": what product/presentation it targets, which customer
    types can see it, and when it is valid.

    The actual discount rules live in ``PromocionEscala`` (one-to-many) so a
    single promotion can offer several quantity tiers (e.g. buy 12 -> 5%,
    buy 24 -> 10%) without duplicating the product/dates/client-type setup
    for every tier.

    ``alcance`` controls whether the promotion applies to a single product
    (INDIVIDUAL) or to a combo of products whose line quantities are summed
    before evaluating scales (GRUPO).
    """

    ALCANCE_INDIVIDUAL = 'INDIVIDUAL'
    ALCANCE_GRUPO = 'GRUPO'
    ALCANCE_CHOICES = (
        (ALCANCE_INDIVIDUAL, _('Single product')),
        (ALCANCE_GRUPO, _('Product combo (sum quantities)')),
    )

    # Kept here (rather than only on PromocionEscala) so old imports/choices lookups
    # that only need the benefit-type vocabulary keep working without reaching into escalas.
    TIPO_PERCENT = 'PERCENT'
    TIPO_FIXED = 'FIXED'
    TIPO_FREE_UNITS = 'FREE_UNITS'
    TIPO_PRECIO_ESPECIAL = 'PRECIO_ESPECIAL'
    TIPO_BENEFICIO_CHOICES = (
        (TIPO_PERCENT, _('Percentage')),
        (TIPO_FIXED, _('Fixed dollars per unit')),
        (TIPO_FREE_UNITS, _('Free units')),
        (TIPO_PRECIO_ESPECIAL, _('Special unit price')),
    )

    nombre = models.CharField(max_length=150, verbose_name=_('Name'))
    descripcion = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Customer description'),
        help_text=_('Short text shown in the catalog, e.g. "Buy 10 cases and get 15% off".'),
    )
    alcance = models.CharField(
        max_length=20,
        choices=ALCANCE_CHOICES,
        default=ALCANCE_INDIVIDUAL,
        verbose_name=_('Scope'),
    )
    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name='promociones',
        verbose_name=_('Product'),
        null=True,
        blank=True,
    )
    presentacion = models.ForeignKey(
        Presentacion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='promociones',
        verbose_name=_('Presentation'),
        help_text=_('Leave empty to apply to any presentation of the product.'),
    )
    tipos_cliente = models.ManyToManyField(
        'clientes.TipoCliente',
        blank=True,
        related_name='promociones',
        verbose_name=_('Customer types'),
        help_text=_('Leave empty to apply to every customer type.'),
    )
    fecha_inicio = models.DateTimeField(null=True, blank=True, verbose_name=_('Start date'))
    fecha_fin = models.DateTimeField(null=True, blank=True, verbose_name=_('End date'))
    activa = models.BooleanField(default=True, verbose_name=_('Active'))
    imagen = models.ImageField(
        upload_to='promociones/',
        blank=True,
        null=True,
        verbose_name=_('Combo image'),
        help_text=_('Optional representative image shown on combo cards in the catalog.'),
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Promotion')
        verbose_name_plural = _('Promotions')
        ordering = ['-activa', '-creada_en']

    def __str__(self):
        return self.nombre

    def clean(self):
        if self.alcance == self.ALCANCE_INDIVIDUAL and not self.producto_id:
            raise ValidationError({'producto': _('Select a product for an individual promotion.')})
        if self.presentacion_id and self.producto_id and self.presentacion.producto_id != self.producto_id:
            raise ValidationError({'presentacion': _('The presentation must belong to the selected product.')})
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValidationError({'fecha_fin': _('End date cannot be earlier than start date.')})

    @property
    def es_grupo(self):
        return self.alcance == self.ALCANCE_GRUPO

    def texto_catalogo(self):
        return (self.descripcion or self.nombre or '').strip()

    def aplica_a_cliente(self, cliente):
        """A promotion with no configured customer types applies to everyone."""
        tipo_id = getattr(cliente, 'tipo_cliente_id', None) if cliente is not None else None
        tipos_ids = {tc.id for tc in self.tipos_cliente.all()}
        if not tipos_ids:
            return True
        return tipo_id is not None and tipo_id in tipos_ids

    @property
    def escala_minima(self):
        """Lowest quantity tier, used for catalog badges (e.g. "Minimum: 5 units")."""
        return min(self.escalas.all(), key=lambda escala: escala.cantidad_minima, default=None)


class PromocionProducto(models.Model):
    """One product (and optional presentation) included in a combo promotion."""

    promocion = models.ForeignKey(
        Promocion,
        on_delete=models.CASCADE,
        related_name='productos_grupo',
        verbose_name=_('Promotion'),
    )
    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name='promociones_grupo',
        verbose_name=_('Product'),
    )
    presentacion = models.ForeignKey(
        Presentacion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='promociones_grupo',
        verbose_name=_('Presentation'),
        help_text=_('Leave empty to include every presentation of this product.'),
    )

    class Meta:
        verbose_name = _('Promotion product')
        verbose_name_plural = _('Promotion products')
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(
                fields=['promocion', 'producto', 'presentacion'],
                name='uniq_promocion_grupo_producto_presentacion',
            ),
        ]

    def __str__(self):
        if self.presentacion_id:
            return f'{self.producto.nombre} ({self.presentacion.nombre})'
        return self.producto.nombre

    def clean(self):
        if self.presentacion_id and self.presentacion.producto_id != self.producto_id:
            raise ValidationError({'presentacion': _('The presentation must belong to the selected product.')})


class PromocionEscala(models.Model):
    """One quantity tier ("scale") of a Promocion, e.g. "buy 24 -> 10% off"."""

    TIPO_PERCENT = Promocion.TIPO_PERCENT
    TIPO_FIXED = Promocion.TIPO_FIXED
    TIPO_FREE_UNITS = Promocion.TIPO_FREE_UNITS
    TIPO_PRECIO_ESPECIAL = Promocion.TIPO_PRECIO_ESPECIAL
    TIPO_BENEFICIO_CHOICES = Promocion.TIPO_BENEFICIO_CHOICES

    promocion = models.ForeignKey(
        Promocion,
        on_delete=models.CASCADE,
        related_name='escalas',
        verbose_name=_('Promotion'),
    )
    cantidad_minima = models.PositiveIntegerField(
        default=1,
        verbose_name=_('Minimum quantity'),
    )
    tipo_beneficio = models.CharField(
        max_length=20,
        choices=TIPO_BENEFICIO_CHOICES,
        default=TIPO_PERCENT,
        verbose_name=_('Benefit type'),
    )
    valor_beneficio = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('Benefit value'),
        help_text=_(
            'Percentage (e.g. 15), dollars per unit (e.g. 2.00), or special unit price, '
            'depending on the benefit type. Not used for Free units.'
        ),
    )
    unidades_gratis = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_('Free units'),
        help_text=_('Only used when the benefit type is "Free units", e.g. buy 10 -> 1 free.'),
    )
    presentacion_regalo = models.ForeignKey(
        'Presentacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='promociones_escala_regalo',
        verbose_name=_('Free product presentation'),
        help_text=_(
            'Optional. When set, Free units grants this presentation as a FREE line '
            '(e.g. buy 120 of product A, receive 1 free case of product B). '
            'When empty, Free units stays as an equivalent discount on the same product.'
        ),
    )
    orden = models.PositiveSmallIntegerField(default=0, verbose_name=_('Display order'))

    class Meta:
        verbose_name = _('Promotion scale')
        verbose_name_plural = _('Promotion scales')
        ordering = ['cantidad_minima']
        constraints = [
            models.UniqueConstraint(
                fields=['promocion', 'cantidad_minima'],
                name='uniq_promocion_escala_cantidad',
            ),
        ]

    def __str__(self):
        return f'{self.promocion.nombre} - {self.cantidad_minima}+ -> {self.texto_beneficio()}'

    def clean(self):
        if self.cantidad_minima is None or self.cantidad_minima < 1:
            raise ValidationError({'cantidad_minima': _('Minimum quantity must be at least 1.')})

        if self.tipo_beneficio == self.TIPO_FREE_UNITS:
            if not self.unidades_gratis or self.unidades_gratis < 1:
                raise ValidationError({'unidades_gratis': _('Enter how many free units are granted.')})
            self.valor_beneficio = None
            return

        self.unidades_gratis = None
        self.presentacion_regalo = None
        valor = _quantize_money(self.valor_beneficio or 0)
        if valor <= 0:
            raise ValidationError({'valor_beneficio': _('Benefit value must be greater than zero.')})
        if self.tipo_beneficio == self.TIPO_PERCENT and valor > Decimal('100'):
            raise ValidationError({'valor_beneficio': _('Percentage benefit cannot exceed 100.')})
        self.valor_beneficio = valor

    def texto_beneficio(self):
        if self.tipo_beneficio == self.TIPO_PERCENT:
            return f'{self.valor_beneficio}% {_("off")}'
        if self.tipo_beneficio == self.TIPO_FIXED:
            return f'${self.valor_beneficio} {_("off per unit")}'
        if self.tipo_beneficio == self.TIPO_FREE_UNITS:
            if self.presentacion_regalo_id:
                gift = self.presentacion_regalo
                gift_label = f'{gift.producto.nombre} ({gift.nombre_empaque_cliente})'
                return str(_('%(units)s free %(product)s') % {
                    'units': self.unidades_gratis,
                    'product': gift_label,
                })
            return str(_('%(units)s free unit(s)') % {'units': self.unidades_gratis})
        if self.tipo_beneficio == self.TIPO_PRECIO_ESPECIAL:
            return str(_('Special price: $%(price)s') % {'price': self.valor_beneficio})
        return ''