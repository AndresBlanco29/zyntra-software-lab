from django.contrib import admin

from .models import Delivery, DeliveryEvidencePhoto, DeliveryNotificationLog, DeliveryPayment, Invoice, InvoiceItem, NotaAjuste, NotaAjusteItem


class InvoiceItemInline(admin.TabularInline):
	model = InvoiceItem
	extra = 0


class NotaAjusteItemInline(admin.TabularInline):
	model = NotaAjusteItem
	extra = 0
	fields = ('invoice_item', 'presentacion', 'contenido_fraccionado', 'descripcion', 'cantidad', 'monto_unitario', 'total')


class DeliveryEvidencePhotoInline(admin.TabularInline):
	model = DeliveryEvidencePhoto
	extra = 0


class DeliveryNotificationLogInline(admin.TabularInline):
	model = DeliveryNotificationLog
	extra = 0
	readonly_fields = ('channel', 'status', 'target', 'message', 'error_message', 'created_at')
	can_delete = False


class DeliveryPaymentInline(admin.TabularInline):
	model = DeliveryPayment
	extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
	list_display = ('numero', 'pedido', 'cliente', 'metodo_entrega', 'driver', 'estado', 'qb_payment_status', 'saldo_cliente', 'despachador_notificado', 'creada_en')
	search_fields = ('numero', 'cliente__nombre_empresa', 'pedido__id')
	list_filter = ('estado', 'metodo_entrega', 'despachador_notificado')
	inlines = [InvoiceItemInline]


@admin.register(NotaAjuste)
class NotaAjusteAdmin(admin.ModelAdmin):
	list_display = ('numero', 'cliente', 'invoice', 'tipo_documento', 'estado', 'motivo', 'tipo_credito', 'total', 'inventario_estado', 'creada_en')
	search_fields = ('numero', 'invoice__numero', 'cliente__nombre_empresa')
	list_filter = ('tipo_documento', 'estado', 'motivo', 'tipo_credito', 'inventario_estado')
	inlines = [NotaAjusteItemInline]


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
	list_display = ('invoice', 'driver', 'estado', 'estado_pago', 'metodo_pago', 'client_blocked_on_delivery', 'delivered_at')
	search_fields = ('invoice__numero', 'driver__username', 'invoice__cliente__nombre_empresa', 'recibido_por')
	list_filter = ('estado', 'estado_pago', 'metodo_pago', 'client_blocked_on_delivery')
	inlines = [DeliveryPaymentInline, DeliveryEvidencePhotoInline, DeliveryNotificationLogInline]
