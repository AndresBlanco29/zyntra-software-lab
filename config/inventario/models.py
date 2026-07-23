from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from config.integrations.quickbooks.constants import QUICKBOOKS_SYNC_STATUS_CHOICES, QUICKBOOKS_SYNC_STATUS_PENDING
from config.productos.models import Presentacion, Producto


class StockPresentacion(models.Model):
	presentacion = models.OneToOneField(Presentacion, on_delete=models.CASCADE, related_name='stock_operativo')
	# Quick Inventory: packages imported from QuickBooks QtyOnHand. Never mutated by local sales.
	stock_fisico = models.IntegerField(
		default=0,
		help_text=_('Quick Inventory from QuickBooks (packages). May be negative when QuickBooks reports oversold quantity.'),
	)
	# Legacy allocation counter; Available no longer uses this field.
	stock_reservado = models.PositiveIntegerField(
		default=0,
		help_text=_('Legacy reserved packages counter (not used for Available).'),
	)
	# Legacy cached value; Available is computed via inventario.availability.
	stock_disponible = models.IntegerField(
		default=0,
		help_text=_('Legacy cached available. Prefer dual-ledger Available from inventario.availability.'),
	)
	actualizado_en = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('presentacion__producto__nombre', 'presentacion__nombre')

	def __str__(self):
		return f'{self.presentacion} | QI: {self.stock_fisico} | Legacy disponible: {self.stock_disponible}'

	@property
	def quick_inventory(self):
		return int(self.stock_fisico or 0)

	def clean(self):
		# Available is computed outside this model; keep legacy fields consistent for old rows.
		expected_available = int(self.stock_fisico or 0) - int(self.stock_reservado or 0)
		if self.stock_disponible != expected_available:
			raise ValidationError(_('Legacy available stock must match Quick Inventory minus legacy reserved.'))

	def save(self, *args, **kwargs):
		self.stock_disponible = int(self.stock_fisico or 0) - int(self.stock_reservado or 0)
		super().save(*args, **kwargs)

	def computed_stock_disponible(self):
		"""Prefer dual-ledger Available; fall back to legacy QI - reserved for isolated use."""
		from config.inventario.availability import available_for_presentacion

		presentacion_id = getattr(self, 'presentacion_id', None)
		if presentacion_id:
			return available_for_presentacion(presentacion_id)
		return int(self.stock_fisico or 0) - int(self.stock_reservado or 0)

	def packages_available_for_picking(self, reserved_for_item=0):
		"""Packages usable for this line: dual-ledger Available + this line's open-order qty."""
		reserved_for_item = max(int(reserved_for_item or 0), 0)
		return max(self.computed_stock_disponible() + reserved_for_item, 0)


class StockProductoFraccionado(models.Model):
	producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='stocks_fraccionados')
	contenido = models.CharField(max_length=50)
	stock_fisico = models.PositiveIntegerField(default=0)
	actualizado_en = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('producto__nombre', 'contenido')
		unique_together = ('producto', 'contenido')

	def __str__(self):
		return f'{self.producto} | {self.contenido}: {self.stock_fisico}'


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
		('CONSOLIDACION_FRACCIONADA', _('Fractional stock consolidation')),
		('DESCONSOLIDACION_FRACCIONADA', _('Fractional stock deconsolidation')),
		('RESERVA_PEDIDO', _('Order reservation')),
		('LIBERACION_PEDIDO', _('Order reservation release')),
		('SALIDA_PICKING', _('Picking deduction')),
		('SALIDA_REGALO', _('Free promotional product deduction')),
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
	stock_fisico_anterior = models.IntegerField(default=0)
	stock_fisico_posterior = models.IntegerField(default=0)
	stock_reservado_anterior = models.PositiveIntegerField(default=0)
	stock_reservado_posterior = models.PositiveIntegerField(default=0)
	stock_disponible_anterior = models.IntegerField(default=0)
	stock_disponible_posterior = models.IntegerField(default=0)
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


class Proveedor(models.Model):
	nombre = models.CharField(max_length=255, unique=True)
	email = models.EmailField(blank=True)
	telefono = models.CharField(max_length=40, blank=True)
	company_name = models.CharField(max_length=255, blank=True)
	balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	notas = models.TextField(blank=True)
	activo = models.BooleanField(default=True)
	quickbooks_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
	sync_status = models.CharField(
		max_length=20,
		choices=QUICKBOOKS_SYNC_STATUS_CHOICES,
		default=QUICKBOOKS_SYNC_STATUS_PENDING,
		db_index=True,
	)
	last_synced_at = models.DateTimeField(blank=True, null=True)
	creado_en = models.DateTimeField(auto_now_add=True)
	actualizado_en = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('nombre', 'id')
		verbose_name = _('Supplier')
		verbose_name_plural = _('Suppliers')

	def __str__(self):
		return self.nombre


class CompraProveedor(models.Model):
	STATUS_DRAFT = 'BORRADOR'
	STATUS_SENT = 'ENVIADA'
	STATUS_RECEIVED = 'RECIBIDA'
	STATUS_CANCELLED = 'CANCELADA'

	STATUS_CHOICES = (
		(STATUS_DRAFT, _('Draft')),
		(STATUS_SENT, _('Sent')),
		(STATUS_RECEIVED, _('Received')),
		(STATUS_CANCELLED, _('Cancelled')),
	)

	proveedor = models.ForeignKey(
		Proveedor,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='compras',
	)
	proveedor_nombre = models.CharField(max_length=255)
	proveedor_email = models.EmailField(blank=True)
	proveedor_telefono = models.CharField(max_length=40, blank=True)
	po_number = models.CharField(max_length=100, blank=True, unique=True)
	bill_number = models.CharField(max_length=100, blank=True)
	fecha_compra = models.DateField(default=timezone.localdate)
	fecha_vencimiento = models.DateField(blank=True, null=True)
	notas = models.TextField(blank=True)
	estado = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
	subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	inventory_applied = models.BooleanField(default=False)
	inventory_received_at = models.DateTimeField(blank=True, null=True)
	inventory_received_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='compras_proveedor_recibidas',
	)
	quickbooks_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
	sync_status = models.CharField(
		max_length=20,
		choices=QUICKBOOKS_SYNC_STATUS_CHOICES,
		default=QUICKBOOKS_SYNC_STATUS_PENDING,
		db_index=True,
	)
	last_synced_at = models.DateTimeField(blank=True, null=True)
	creado_por = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='compras_proveedor_creadas',
	)
	creado_en = models.DateTimeField(auto_now_add=True)
	actualizado_en = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('-fecha_compra', '-id')

	def __str__(self):
		identifier = self.po_number or self.bill_number or f'Compra {self.pk}'
		return f'{identifier} | {self.proveedor_nombre}'

	@property
	def accounting_locked(self):
		return bool(self.quickbooks_id or self.sync_status == 'SYNCED')

	def recalcular_totales(self, *, save=True):
		total = sum((linea.subtotal for linea in self.lineas.all()), start=Decimal('0.00'))
		self.subtotal = total
		self.total = total
		if save and self.pk:
			self.save(update_fields=['subtotal', 'total', 'actualizado_en'])
		return total

	def save(self, *args, **kwargs):
		is_new = self.pk is None
		super().save(*args, **kwargs)
		if is_new and not self.po_number:
			self.po_number = f'PO-{timezone.now().year}-{self.pk:06d}'
			super().save(update_fields=['po_number'])


class CompraProveedorLinea(models.Model):
	compra = models.ForeignKey(CompraProveedor, on_delete=models.CASCADE, related_name='lineas')
	presentacion = models.ForeignKey(Presentacion, on_delete=models.PROTECT, related_name='compras_proveedor_lineas')
	cantidad = models.PositiveIntegerField(default=1)
	costo_unitario = models.DecimalField(max_digits=12, decimal_places=2)
	subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	descripcion = models.CharField(max_length=255, blank=True)

	class Meta:
		ordering = ('id',)

	def clean(self):
		if self.cantidad <= 0:
			raise ValidationError(_('Quantity must be greater than zero.'))
		if self.costo_unitario < 0:
			raise ValidationError(_('Unit cost cannot be negative.'))

	def save(self, *args, **kwargs):
		self.subtotal = self.cantidad * self.costo_unitario
		super().save(*args, **kwargs)
