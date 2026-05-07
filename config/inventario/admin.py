from django.contrib import admin

from .models import InventarioMovimiento, StockPresentacion, StockProductoFraccionado


@admin.register(StockPresentacion)
class StockPresentacionAdmin(admin.ModelAdmin):
	list_display = ('presentacion', 'stock_fisico', 'stock_reservado', 'stock_disponible', 'actualizado_en')
	search_fields = ('presentacion__producto__nombre', 'presentacion__nombre')
	list_filter = ('presentacion__producto__categoria',)


@admin.register(InventarioMovimiento)
class InventarioMovimientoAdmin(admin.ModelAdmin):
	list_display = ('creado_en', 'presentacion', 'categoria', 'tipo', 'cantidad', 'delta_fisico', 'delta_reservado', 'referencia')
	search_fields = ('presentacion__producto__nombre', 'presentacion__nombre', 'referencia')
	list_filter = ('categoria', 'tipo')
	readonly_fields = ('creado_en',)


@admin.register(StockProductoFraccionado)
class StockProductoFraccionadoAdmin(admin.ModelAdmin):
	list_display = ('producto', 'contenido', 'stock_fisico', 'actualizado_en')
	search_fields = ('producto__nombre', 'contenido')
	list_filter = ('contenido',)
