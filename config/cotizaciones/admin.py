from django.contrib import admin
from .models import Cotizacion, CotizacionItem

class CotizacionItemInline(admin.TabularInline):
    model = CotizacionItem
    extra = 1

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):

    list_display = (
        'id',
        'cliente',
        'vendedor',
        'fecha',
        'estado',
        'total'
    )

    list_filter = (
        'estado',
        'fecha'
    )

    inlines = [CotizacionItemInline]

@admin.register(CotizacionItem)
class CotizacionItemAdmin(admin.ModelAdmin):

    list_display = (
        'cotizacion',
        'presentacion',
        'cantidad',
        'precio',
        'subtotal'
    )