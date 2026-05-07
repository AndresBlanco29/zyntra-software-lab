from django.contrib import admin
from .models import HomeContenido, Testimonio


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


@admin.register(HomeContenido)
class HomeContenidoAdmin(admin.ModelAdmin):

    fieldsets = (
        ("Hero", {
            "fields": (
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
            )
        }),
        ("CTA", {
            "fields": (
                "cta_titulo",
                "cta_titulo_en",
                "cta_boton_registro_texto",
                "cta_boton_registro_texto_en",
                "cta_boton_catalogo_texto",
                "cta_boton_catalogo_texto_en",
            )
        }),
        ("Footer", {
            "fields": (
                "footer_empresa_titulo",
                "footer_empresa_titulo_en",
                "footer_empresa_descripcion",
                "footer_empresa_descripcion_en",
                "footer_contacto_titulo",
                "footer_contacto_titulo_en",
                "footer_contacto_direccion_linea_1",
                "footer_contacto_direccion_linea_2",
                "footer_contacto_email",
                "footer_contacto_telefono",
                "activo",
            )
        }),
    )

    list_display = ("id", "footer_empresa_titulo", "footer_contacto_email", "activo", "actualizado")
    list_filter = ("activo",)
    search_fields = ("footer_empresa_titulo", "footer_contacto_email", "footer_contacto_telefono")