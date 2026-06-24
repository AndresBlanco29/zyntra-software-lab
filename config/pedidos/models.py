from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion
from config.productos.models import Presentacion


class Pedido(models.Model):

	ESTADO_CHOICES = (
		('RECIBIDO', 'Recibido'),
		('EN_GESTION', 'En gestion'),
		('LISTO_PARA_PICKING', 'Listo para picking'),
		('PARA_VERIFICAR', 'Para verificar'),
		('VERIFICADO_AJUSTADO', 'Verificado y ajustado'),
		('INVOICE_GENERADA', 'Invoice generada'),
		('DESPACHADO', 'Despachado'),
		('CANCELADO', 'Cancelado'),
	)

	ORIGEN_CHOICES = (
		('CLIENTE', 'Cliente'),
		('VENDEDOR', 'Vendedor'),
		('BACKOFFICE', 'BackOffice'),
	)

	cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='pedidos')
	vendedor = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='pedidos_generados',
		limit_choices_to={'role': 'vendedor'},
	)
	seleccionador = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='picking_tickets_asignados',
		limit_choices_to={'role': 'seleccionador'},
	)
	cotizacion = models.OneToOneField(
		Cotizacion,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='pedido_generado',
	)
	origen = models.CharField(max_length=20, choices=ORIGEN_CHOICES)
	canal_toma = models.CharField(max_length=20, blank=True, default='')
	estado = models.CharField(max_length=30, choices=ESTADO_CHOICES, default='RECIBIDO')
	nota_cliente = models.TextField(blank=True)
	nota_backoffice = models.TextField(blank=True)
	nota_seleccionador = models.TextField(blank=True)
	nota_seleccionador_resuelta = models.BooleanField(default=False)
	picking_bloqueado = models.BooleanField(default=False)
	picking_asignado_en = models.DateTimeField(blank=True, null=True)
	picking_verificado_en = models.DateTimeField(blank=True, null=True)
	acepta_terminos = models.BooleanField(default=False)
	acepta_terminos_en = models.DateTimeField(blank=True, null=True)
	total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	creada_en = models.DateTimeField(auto_now_add=True)
	actualizada_en = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('-creada_en',)
		db_table = 'pedidos_pedidocompra'

	def __str__(self):
		return f"Pedido #{self.id} - {self.cliente.nombre_empresa}"

	@property
	def tiene_nota_picking_pendiente(self):
		return bool(self.nota_seleccionador.strip()) and not self.nota_seleccionador_resuelta

	def clean(self):
		if self.seleccionador_id and getattr(self.seleccionador, 'role', '') != 'seleccionador':
			raise ValidationError({'seleccionador': _('Only selector users can be assigned to a picking ticket.')})
		if self.estado == 'PARA_VERIFICAR' and not self.seleccionador_id:
			raise ValidationError({'seleccionador': _('A selector must be assigned before verification starts.')})

	def save(self, *args, **kwargs):
		self.picking_bloqueado = self.tiene_nota_picking_pendiente
		super().save(*args, **kwargs)


class PedidoItem(models.Model):

	pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='items')
	presentacion = models.ForeignKey(Presentacion, on_delete=models.CASCADE)
	selector_original_presentacion = models.ForeignKey(
		Presentacion,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='pedido_items_selector_originales',
	)
	selector_added_by_picker = models.BooleanField(default=False)
	cantidad_solicitada = models.PositiveIntegerField(default=1)
	cantidad_reservada_inventario = models.PositiveIntegerField(default=0)
	cantidad_inventario_aplicada = models.PositiveIntegerField(default=0)
	cantidad = models.PositiveIntegerField(default=1)
	precio = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	def __str__(self):
		return f"{self.presentacion.producto.nombre} x {self.cantidad}"

	@property
	def selector_changed_presentation(self):
		return bool(self.selector_original_presentacion_id and self.selector_original_presentacion_id != self.presentacion_id)

	@property
	def selector_changed_quantity(self):
		return int(self.cantidad or 0) != int(self.cantidad_solicitada or 0)

	@property
	def cantidad_solicitada_documentada(self):
		current_quantity = int(self.cantidad_solicitada or 0)
		if current_quantity > 0:
			return current_quantity

		movimientos_prefetch = getattr(self, '_prefetched_objects_cache', {}).get('movimientos_inventario')
		if movimientos_prefetch is not None:
			reservation_moves = [
				movimiento for movimiento in movimientos_prefetch
				if movimiento.tipo == 'RESERVA_PEDIDO' and int(movimiento.cantidad or 0) > 0
			]
			if reservation_moves:
				reservation_moves.sort(key=lambda movimiento: (movimiento.creado_en, movimiento.id))
				return int(reservation_moves[0].cantidad)

		reservation_move = self.movimientos_inventario.filter(
			tipo='RESERVA_PEDIDO',
			cantidad__gt=0,
		).order_by('creado_en', 'id').first()
		if reservation_move:
			return int(reservation_move.cantidad or 0)

		pedido = getattr(self, 'pedido', None)
		cotizacion = getattr(pedido, 'cotizacion', None) if pedido else None
		if cotizacion is not None:
			cotizacion_item = cotizacion.items.filter(
				presentacion_id=self.presentacion_id,
				cantidad__gt=0,
			).order_by('id').first()
			if cotizacion_item:
				return int(cotizacion_item.cantidad or 0)

		return int(self.cantidad or 0)

	@property
	def selector_has_changes(self):
		return bool(self.selector_added_by_picker or self.selector_changed_presentation or self.selector_changed_quantity)


class PedidoEditLock(models.Model):
	pedido = models.OneToOneField(Pedido, on_delete=models.CASCADE, related_name='edit_lock')
	locked_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name='pedido_edit_locks',
	)
	locked_at = models.DateTimeField(auto_now_add=True)
	last_seen_at = models.DateTimeField(auto_now=True)

	class Meta:
		db_table = 'pedidos_pedidoeditlock'
		verbose_name = _('Sales order edit lock')
		verbose_name_plural = _('Sales order edit locks')

	def __str__(self):
		return f'Pedido #{self.pedido_id} locked by {self.locked_by_id}'
