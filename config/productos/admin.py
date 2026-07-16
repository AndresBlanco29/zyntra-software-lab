from django.contrib import admin
from django.core.exceptions import ValidationError
from .models import Producto, Presentacion, Categoria, Marca, ConfiguracionPrecios, ConfiguracionDescuentos, Promocion


class PresentacionInline(admin.TabularInline):
    model = Presentacion
    extra = 1
    fields = ('nombre', 'unidades', 'tipo_contenido', 'costo', 'qb_price', 'peso_por_caja', 'precio_1')
    readonly_fields = ('qb_price',)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):

    list_display = (
        "nombre",
        "codigo_barras",
        "categoria",
        "marca",
        "activo",
        "destacado",
        "descuento"
    )

    list_filter = (
        "categoria",
        "marca",
        "activo",
        "destacado",
    )

    search_fields = (
        "nombre",
        "descripcion",
        "codigo_barras",
    )

    fieldsets = (
        ("Información General", {
            "fields": ("nombre", "nombre_en", "codigo_barras"),
            "description": "Los campos marcados con * son requeridos"
        }),
        ("Descripción", {
            "fields": ("descripcion", "descripcion_en"),
            "classes": ("collapse",)
        }),
        ("Clasificación", {
            "fields": ("categoria", "marca")
        }),
        ("Detalles del Producto", {
            "fields": ("imagen", "descuento", "activo", "destacado")
        }),
        ("Integraciones", {
            "fields": ("quickbooks_id",),
            "classes": ("collapse",)
        }),
    )

    inlines = [PresentacionInline]

    def save_model(self, request, obj, form, change):
        """Validar que el código de barras sea obligatorio"""
        if not obj.codigo_barras or obj.codigo_barras.strip() == '':
            raise ValidationError(
                "El código de barras es requerido. Por favor ingresa un valor único."
            )
        super().save_model(request, obj, form, change)


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):

    list_display = ("nombre",)

    search_fields = ("nombre",)


@admin.register(Marca)
class MarcaAdmin(admin.ModelAdmin):

    list_display = ("nombre",)

    search_fields = ("nombre",)


@admin.register(ConfiguracionPrecios)
class ConfiguracionPreciosAdmin(admin.ModelAdmin):
    list_display = (
        "porcentaje_1",
        "porcentaje_2",
        "porcentaje_3",
        "porcentaje_4",
        "porcentaje_5",
    )

    def has_add_permission(self, request):
        return not ConfiguracionPrecios.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConfiguracionDescuentos)
class ConfiguracionDescuentosAdmin(admin.ModelAdmin):
    list_display = tuple(f"descuento_{index}" for index in range(1, 11))

    def has_add_permission(self, request):
        return not ConfiguracionDescuentos.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Promocion)
class PromocionAdmin(admin.ModelAdmin):
    list_display = (
        'nombre',
        'producto',
        'presentacion',
        'cantidad_minima',
        'tipo_beneficio',
        'valor_beneficio',
        'activa',
        'fecha_inicio',
        'fecha_fin',
    )
    list_filter = ('activa', 'tipo_beneficio')
    search_fields = ('nombre', 'descripcion', 'producto__nombre')