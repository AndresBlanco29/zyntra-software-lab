from django.contrib import admin
from .models import Cliente, TipoCliente

admin.site.register(Cliente)


@admin.register(TipoCliente)
class TipoClienteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo', 'activo', 'orden')
    list_filter = ('activo',)
    search_fields = ('nombre', 'nombre_en', 'codigo')
    ordering = ('orden', 'nombre')
