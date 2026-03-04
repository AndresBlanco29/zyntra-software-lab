from django.contrib import admin
from .models import Producto, Presentacion, Categoria, Marca


class PresentacionInline(admin.TabularInline):
    model = Presentacion
    extra = 1


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):

    list_display = (
        "nombre",
        "categoria",
        "marca",
        "activo",
    )

    list_filter = (
        "categoria",
        "marca",
        "activo",
    )

    search_fields = (
        "nombre",
        "descripcion",
    )

    inlines = [PresentacionInline]


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):

    list_display = ("nombre",)

    search_fields = ("nombre",)


@admin.register(Marca)
class MarcaAdmin(admin.ModelAdmin):

    list_display = ("nombre",)

    search_fields = ("nombre",)