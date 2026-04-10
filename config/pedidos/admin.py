from django.contrib import admin

from .models import Pedido, PedidoItem


class PedidoItemInline(admin.TabularInline):
	model = PedidoItem
	extra = 0


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
	list_display = ('id', 'cliente', 'origen', 'estado', 'total', 'creada_en')
	list_filter = ('estado', 'origen', 'creada_en')
	search_fields = ('cliente__nombre_empresa', 'cliente__usuario__email')
	inlines = [PedidoItemInline]


@admin.register(PedidoItem)
class PedidoItemAdmin(admin.ModelAdmin):
	list_display = ('pedido', 'presentacion', 'cantidad', 'precio', 'subtotal')
