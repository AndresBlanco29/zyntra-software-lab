from django.contrib import admin
from .models import Testimonio


@admin.register(Testimonio)
class TestimonioAdmin(admin.ModelAdmin):

    fieldsets = (
        (None, {
            "fields": (
                "nombre",
                "negocio",
                "negocio_en",
                "comentario",
                "comentario_en",
                "estrellas",
                "foto",
                "activo",
                "orden",
            )
        }),
    )

    list_display = (
        "nombre",
        "negocio",
        "estrellas",
        "activo",
        "orden"
    )

    list_editable = (
        "activo",
        "orden"
    )

    list_filter = (
        "activo",
        "estrellas"
    )

    search_fields = (
        "nombre",
        "negocio",
        "negocio_en",
        "comentario",
        "comentario_en"
    )