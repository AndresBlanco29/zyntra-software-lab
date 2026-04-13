from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from config.productos.models import Presentacion


class StockPresentacion(models.Model):
	presentacion = models.OneToOneField(Presentacion, on_delete=models.CASCADE, related_name='stock_operativo')
	stock_fisico = models.PositiveIntegerField(default=0)
	stock_reservado = models.PositiveIntegerField(default=0)
	stock_disponible = models.PositiveIntegerField(default=0)
	actualizado_en = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('presentacion__producto__nombre', 'presentacion__nombre')

	def __str__(self):
		return f'{self.presentacion} | Fisico: {self.stock_fisico} | Disponible: {self.stock_disponible}'

	def clean(self):
		expected_available = self.stock_fisico - self.stock_reservado
		if expected_available < 0:
			raise ValidationError(_('Reserved stock cannot exceed physical stock.'))
		if self.stock_disponible != expected_available:
			raise ValidationError(_('Available stock must match physical stock minus reserved stock.'))

	def save(self, *args, **kwargs):
		self.stock_disponible = self.stock_fisico - self.stock_reservado
		super().save(*args, **kwargs)


class InventarioMovimiento(models.Model):
	CATEGORY_CHOICES = (
		('ENTRADA', _('Entry')),
		('SALIDA', _('Exit')),
		('AJUSTE', _('Adjustment')),
		('RESERVA', _('Reservation')),
	)

	TYPE_CHOICES = (
		('ENTRADA_MANUAL', _('Manual entry')),
		('SALIDA_MANUAL', _('Manual exit')),
		('AJUSTE_POSITIVO', _('Positive adjustment')),
		('AJUSTE_NEGATIVO', _('Negative adjustment')),
		('RESERVA_PEDIDO', _('Order reservation')),
		('LIBERACION_PEDIDO', _('Order reservation release')),
		('SALIDA_PICKING', _('Picking deduction')),
		('AJUSTE_PICKING', _('Picking adjustment')),
		('ENTRADA_NOTA_CREDITO', _('Credit note return')),
		('REVERSO_NOTA_CREDITO', _('Credit note reversal')),
		('ANULACION_PEDIDO', _('Order cancellation reversal')),
	)

	presentacion = models.ForeignKey(Presentacion, on_delete=models.PROTECT, related_name='movimientos_inventario')
	stock = models.ForeignKey(StockPresentacion, on_delete=models.PROTECT, related_name='movimientos')
	categoria = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
	tipo = models.CharField(max_length=30, choices=TYPE_CHOICES)
	cantidad = models.PositiveIntegerField(default=0)
	delta_fisico = models.IntegerField(default=0)
	delta_reservado = models.IntegerField(default=0)
	stock_fisico_anterior = models.PositiveIntegerField(default=0)
	stock_fisico_posterior = models.PositiveIntegerField(default=0)
	stock_reservado_anterior = models.PositiveIntegerField(default=0)
	stock_reservado_posterior = models.PositiveIntegerField(default=0)
	stock_disponible_anterior = models.PositiveIntegerField(default=0)
	stock_disponible_posterior = models.PositiveIntegerField(default=0)
	referencia = models.CharField(max_length=120)
	idempotency_key = models.CharField(max_length=160, blank=True, null=True, unique=True)
	observacion = models.TextField(blank=True)
	pedido = models.ForeignKey('pedidos.Pedido', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_inventario')
	pedido_item = models.ForeignKey('pedidos.PedidoItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_inventario')
	invoice = models.ForeignKey('facturacion.Invoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_inventario')
	nota_ajuste = models.ForeignKey('facturacion.NotaAjuste', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_inventario')
	nota_ajuste_item = models.ForeignKey('facturacion.NotaAjusteItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_inventario')
	creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_inventario_creados')
	creado_en = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ('-creado_en', '-id')

	def __str__(self):
		return f'{self.referencia} | {self.get_tipo_display()} | {self.presentacion_id}'
