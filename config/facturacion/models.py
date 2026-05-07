from decimal import Decimal
from urllib.parse import quote_plus

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from config.clientes.models import Cliente
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Presentacion


COUNTRY_ALIASES_USA = {'usa', 'us', 'eeuu', 'estados unidos', 'united states'}


class Invoice(models.Model):
	DELIVERY_METHOD_CHOICES = (
		('RUTA_DRIVER', _('Route with driver')),
		('LTG', 'LTG'),
		('CUSTOMER_PICK_UP', _('Customer Pick Up')),
	)

	STATUS_CHOICES = (
		('GENERADA', _('Invoice generated')),
		('ANULADA', _('Cancelled')),
	)

	numero = models.CharField(max_length=30, unique=True, blank=True)
	pedido = models.OneToOneField(Pedido, on_delete=models.PROTECT, related_name='invoice')
	cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='invoices')
	metodo_entrega = models.CharField(max_length=30, choices=DELIVERY_METHOD_CHOICES)
	driver = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='invoices_asignadas',
		limit_choices_to={'role': 'driver'},
	)
	estado = models.CharField(max_length=20, choices=STATUS_CHOICES, default='GENERADA')
	subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	credito_cliente_aplicado = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	total_creditos = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	total_debitos = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	total_neto = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	saldo_cliente = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	despachador_notificado = models.BooleanField(default=False)
	notificado_en = models.DateTimeField(blank=True, null=True)
	pdf_generado_en = models.DateTimeField(blank=True, null=True)
	creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoices_creadas')
	creada_en = models.DateTimeField(auto_now_add=True)
	actualizada_en = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('-creada_en',)

	def __str__(self):
		return self.numero or f'Invoice #{self.pk}'

	def clean(self):
		if self.pedido_id and self.pedido.estado not in {'VERIFICADO_AJUSTADO', 'INVOICE_GENERADA'}:
			raise ValidationError({'pedido': _('The invoice can only be generated from a verified and adjusted picking order.')})
		if self.pedido_id and self.pedido.picking_bloqueado:
			raise ValidationError({'pedido': _('The order is blocked by an unresolved selector note.')})
		if self.metodo_entrega == 'RUTA_DRIVER':
			if not self.driver_id:
				raise ValidationError({'driver': _('A driver is required for route deliveries.')})
			if getattr(self.driver, 'role', '') != 'driver':
				raise ValidationError({'driver': _('Only users with driver role can be assigned.')})
		elif self.driver_id:
			raise ValidationError({'driver': _('Driver assignment is only allowed for route deliveries.')})

	def save(self, *args, **kwargs):
		is_new = self.pk is None
		super().save(*args, **kwargs)
		if is_new and not self.numero:
			self.numero = f'INV-{timezone.now().year}-{self.pk:06d}'
			super().save(update_fields=['numero'])


class Delivery(models.Model):
	STATUS_CHOICES = (
		('ASIGNADA', _('Assigned')),
		('EN_RUTA', _('On route')),
		('ENTREGADA_PAGADA', _('Delivered and paid')),
		('ENTREGADA_SIN_PAGO', _('Delivered without payment')),
	)

	PAYMENT_STATUS_CHOICES = (
		('PENDIENTE', _('Pending')),
		('PAGADO', _('Paid')),
		('NO_PAGADO', _('Unpaid')),
	)

	PAYMENT_METHOD_CHOICES = (
		('CASH', _('Cash')),
		('CHEQUE', _('Cheque')),
		('MIXTO', _('Cash + cheque')),
		('MULTIPLE', _('Multiple methods')),
		('TRANSFERENCIA', _('Transfer')),
		('TARJETA', _('Card')),
		('ZELLE', 'Zelle'),
		('ACH', 'ACH'),
	)

	invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name='delivery')
	driver = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='deliveries',
		limit_choices_to={'role': 'driver'},
	)
	estado = models.CharField(max_length=30, choices=STATUS_CHOICES, default='ASIGNADA')
	estado_pago = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='PENDIENTE')
	metodo_pago = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True)
	monto_pagado = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	monto_pagado_cash = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	monto_pagado_cheque = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	recibido_por = models.CharField(max_length=160, blank=True)
	motivo_no_pago = models.TextField(blank=True)
	notas_driver = models.TextField(blank=True)
	firma_cliente = models.ImageField(upload_to='delivery/signatures/', blank=True, null=True)
	firma_recibida_en = models.DateTimeField(blank=True, null=True)
	cheque_numero = models.CharField(max_length=80, blank=True)
	cheque_banco = models.CharField(max_length=120, blank=True)
	cheque_imagen = models.ImageField(upload_to='delivery/checks/', blank=True, null=True)
	transferencia_referencia = models.CharField(max_length=120, blank=True)
	tarjeta_ultimos_4 = models.CharField(max_length=4, blank=True)
	tarjeta_autorizacion = models.CharField(max_length=80, blank=True)
	zelle_referencia = models.CharField(max_length=120, blank=True)
	zelle_remitente = models.CharField(max_length=160, blank=True)
	ach_referencia = models.CharField(max_length=120, blank=True)
	ach_cuenta_ultimos_4 = models.CharField(max_length=4, blank=True)
	delivery_address = models.CharField(max_length=255)
	delivery_city = models.CharField(max_length=100)
	delivery_state = models.CharField(max_length=100)
	delivery_postal_code = models.CharField(max_length=20, blank=True)
	delivery_country = models.CharField(max_length=100, default='USA')
	current_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
	current_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
	current_accuracy_meters = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
	current_speed_mps = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
	current_heading = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
	location_updated_at = models.DateTimeField(blank=True, null=True)
	client_blocked_on_delivery = models.BooleanField(default=False)
	client_unlocked_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='deliveries_unlocked',
	)
	client_unlocked_at = models.DateTimeField(blank=True, null=True)
	estimated_delivery_at = models.DateTimeField(blank=True, null=True)
	route_started_at = models.DateTimeField(blank=True, null=True)
	delivered_at = models.DateTimeField(blank=True, null=True)
	notifications_sent_at = models.DateTimeField(blank=True, null=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('estado', 'created_at')

	def __str__(self):
		return f'{self.invoice.numero} - {self.get_estado_display()}'

	@property
	def route_address(self):
		parts = [self.delivery_address, self.delivery_city, self.delivery_state, self.delivery_postal_code, self.delivery_country]
		return ', '.join(part for part in parts if part)

	@property
	def route_query_address(self):
		country = (self.delivery_country or '').strip()
		parts = [self.delivery_address, self.delivery_city, self.delivery_state]
		if (country or '').strip().lower() in COUNTRY_ALIASES_USA and self.delivery_postal_code:
			parts.append(self.delivery_postal_code)
		if country:
			parts.append(country)
		return ', '.join(part for part in parts if part)

	@property
	def google_maps_url(self):
		return f'https://www.google.com/maps/dir/?api=1&destination={quote_plus(self.route_query_address)}&travelmode=driving'

	@property
	def has_live_location(self):
		return self.current_latitude is not None and self.current_longitude is not None

	@property
	def live_google_maps_url(self):
		if self.has_live_location:
			return f'https://www.google.com/maps?q={self.current_latitude},{self.current_longitude}'
		return self.google_maps_url

	@property
	def is_completed(self):
		return self.estado in {'ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'}

	@property
	def payment_breakdown(self):
		if self.pk:
			payments = list(self.payments.all())
			if payments:
				return [
					{
						'label': payment.get_metodo_pago_display(),
						'amount': payment.monto,
						'details': payment.payment_details_summary,
						'proof_url': payment.cheque_imagen.url if payment.cheque_imagen else '',
					}
					for payment in payments
				]
		breakdown = []
		if self.monto_pagado_cash > 0:
			breakdown.append({'label': _('Cash'), 'amount': self.monto_pagado_cash})
		if self.monto_pagado_cheque > 0:
			breakdown.append({'label': _('Cheque'), 'amount': self.monto_pagado_cheque})
		if breakdown:
			return breakdown
		if self.monto_pagado > 0:
			label = self.get_metodo_pago_display() if self.metodo_pago else _('Payment')
			return [{'label': label, 'amount': self.monto_pagado}]
		return []

	def clean(self):
		if self.invoice_id:
			if self.invoice.metodo_entrega != 'RUTA_DRIVER':
				raise ValidationError({'invoice': _('Only route invoices can create a delivery assignment.')})
			if self.invoice.driver_id and self.driver_id != self.invoice.driver_id:
				raise ValidationError({'driver': _('Delivery driver must match the invoice driver.')})
		if getattr(self.driver, 'role', '') != 'driver':
			raise ValidationError({'driver': _('Only users with driver role can manage deliveries.')})
		if self.estado_pago == 'PAGADO':
			if not self.metodo_pago:
				raise ValidationError({'metodo_pago': _('A payment method is required when the delivery is paid.')})
			if self.monto_pagado <= 0:
				raise ValidationError({'monto_pagado': _('Paid deliveries must include a payment amount greater than zero.')})
			if self.monto_pagado > (self.invoice.saldo_cliente if self.invoice_id else Decimal('0.00')):
				raise ValidationError({'monto_pagado': _('The paid amount cannot exceed the customer balance.')})
			if not self.recibido_por.strip():
				raise ValidationError({'recibido_por': _('Recipient name is required for delivered orders.')})
			if not self.firma_cliente:
				raise ValidationError({'firma_cliente': _('Customer signature is required to complete the delivery.')})
			self._validate_payment_method_fields()
		if self.estado_pago == 'NO_PAGADO':
			if self.metodo_pago:
				raise ValidationError({'metodo_pago': _('Unpaid deliveries cannot define a payment method.')})
			if not self.motivo_no_pago.strip():
				raise ValidationError({'motivo_no_pago': _('A reason is required when the customer does not pay.')})
			if not self.recibido_por.strip():
				raise ValidationError({'recibido_por': _('Recipient name is required when the customer does not pay.')})
			if not self.firma_cliente:
				raise ValidationError({'firma_cliente': _('Customer signature is required when the customer does not pay.')})

	def _validate_payment_method_fields(self):
		method = self.metodo_pago
		if method == 'CHEQUE':
			if self.monto_pagado_cheque <= 0:
				raise ValidationError({'monto_pagado_cheque': _('Cheque payments must include an amount greater than zero.')})
			if not self.cheque_numero.strip() or not self.cheque_banco.strip() or not self.cheque_imagen:
				raise ValidationError({'cheque_numero': _('Cheque number, bank and cheque image are required for cheque payments.')})
		elif method == 'MIXTO':
			if self.monto_pagado_cash <= 0 or self.monto_pagado_cheque <= 0:
				raise ValidationError({'monto_pagado_cash': _('Cash + cheque payments must include both amounts greater than zero.')})
			if not self.cheque_numero.strip() or not self.cheque_banco.strip() or not self.cheque_imagen:
				raise ValidationError({'cheque_numero': _('Cheque number, bank and cheque image are required for cash + cheque payments.')})
		elif method == 'TRANSFERENCIA':
			if not self.transferencia_referencia.strip():
				raise ValidationError({'transferencia_referencia': _('Transfer reference is required.')})
		elif method == 'TARJETA':
			if len(self.tarjeta_ultimos_4.strip()) != 4 or not self.tarjeta_autorizacion.strip():
				raise ValidationError({'tarjeta_ultimos_4': _('Card last four digits and authorization code are required.')})
		elif method == 'ZELLE':
			if not self.zelle_referencia.strip() or not self.zelle_remitente.strip():
				raise ValidationError({'zelle_referencia': _('Zelle reference and sender are required.')})
		elif method == 'ACH':
			if not self.ach_referencia.strip() or len(self.ach_cuenta_ultimos_4.strip()) != 4:
				raise ValidationError({'ach_referencia': _('ACH reference and account last four digits are required.')})


class DeliveryPayment(models.Model):
	delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name='payments')
	position = models.PositiveSmallIntegerField(default=1)
	metodo_pago = models.CharField(max_length=20, choices=Delivery.PAYMENT_METHOD_CHOICES)
	monto = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	cheque_numero = models.CharField(max_length=80, blank=True)
	cheque_banco = models.CharField(max_length=120, blank=True)
	cheque_imagen = models.ImageField(upload_to='delivery/checks/', blank=True, null=True)
	transferencia_referencia = models.CharField(max_length=120, blank=True)
	tarjeta_ultimos_4 = models.CharField(max_length=4, blank=True)
	tarjeta_autorizacion = models.CharField(max_length=80, blank=True)
	zelle_referencia = models.CharField(max_length=120, blank=True)
	zelle_remitente = models.CharField(max_length=160, blank=True)
	ach_referencia = models.CharField(max_length=120, blank=True)
	ach_cuenta_ultimos_4 = models.CharField(max_length=4, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ('position', 'id')
		constraints = [
			models.UniqueConstraint(fields=('delivery', 'position'), name='unique_delivery_payment_position'),
		]

	def __str__(self):
		return f'{self.delivery.invoice.numero} - {self.get_metodo_pago_display()} ${self.monto}'

	@property
	def payment_details_summary(self):
		if self.metodo_pago == 'CHEQUE':
			parts = []
			if self.cheque_numero:
				parts.append(_('No. %(value)s') % {'value': self.cheque_numero})
			if self.cheque_banco:
				parts.append(self.cheque_banco)
			return ' | '.join(parts)
		if self.metodo_pago == 'TRANSFERENCIA' and self.transferencia_referencia:
			return _('Ref. %(value)s') % {'value': self.transferencia_referencia}
		if self.metodo_pago == 'TARJETA':
			parts = []
			if self.tarjeta_ultimos_4:
				parts.append(_('****%(value)s') % {'value': self.tarjeta_ultimos_4})
			if self.tarjeta_autorizacion:
				parts.append(_('Auth %(value)s') % {'value': self.tarjeta_autorizacion})
			return ' | '.join(parts)
		if self.metodo_pago == 'ZELLE':
			parts = []
			if self.zelle_referencia:
				parts.append(_('Ref. %(value)s') % {'value': self.zelle_referencia})
			if self.zelle_remitente:
				parts.append(self.zelle_remitente)
			return ' | '.join(parts)
		if self.metodo_pago == 'ACH':
			parts = []
			if self.ach_referencia:
				parts.append(_('Ref. %(value)s') % {'value': self.ach_referencia})
			if self.ach_cuenta_ultimos_4:
				parts.append(_('Acct ****%(value)s') % {'value': self.ach_cuenta_ultimos_4})
			return ' | '.join(parts)
		return ''

	def clean(self):
		if self.monto <= 0:
			raise ValidationError({'monto': _('Each payment entry must include an amount greater than zero.')})
		if self.metodo_pago in {'MIXTO', 'MULTIPLE'}:
			raise ValidationError({'metodo_pago': _('Payment entries must use a concrete payment method.')})
		if self.metodo_pago == 'CHEQUE':
			if not self.cheque_numero.strip() or not self.cheque_banco.strip() or not self.cheque_imagen:
				raise ValidationError({'cheque_numero': _('Cheque number, bank and cheque image are required for cheque payments.')})
		elif self.metodo_pago == 'TRANSFERENCIA':
			if not self.transferencia_referencia.strip():
				raise ValidationError({'transferencia_referencia': _('Transfer reference is required.')})
		elif self.metodo_pago == 'TARJETA':
			if len(self.tarjeta_ultimos_4.strip()) != 4 or not self.tarjeta_autorizacion.strip():
				raise ValidationError({'tarjeta_ultimos_4': _('Card last four digits and authorization code are required.')})
		elif self.metodo_pago == 'ZELLE':
			if not self.zelle_referencia.strip() or not self.zelle_remitente.strip():
				raise ValidationError({'zelle_referencia': _('Zelle reference and sender are required.')})
		elif self.metodo_pago == 'ACH':
			if not self.ach_referencia.strip() or len(self.ach_cuenta_ultimos_4.strip()) != 4:
				raise ValidationError({'ach_referencia': _('ACH reference and account last four digits are required.')})


class DeliveryEvidencePhoto(models.Model):
	delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name='evidence_photos')
	image = models.ImageField(upload_to='delivery/evidence/')
	caption = models.CharField(max_length=255, blank=True)
	uploaded_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ('uploaded_at',)

	def __str__(self):
		return f'{self.delivery.invoice.numero} evidence {self.pk}'


class DeliveryNotificationLog(models.Model):
	CHANNEL_CHOICES = (
		('EMAIL', 'Email'),
		('SMS', 'SMS'),
		('WHATSAPP', 'WhatsApp'),
	)

	STATUS_CHOICES = (
		('SENT', _('Sent')),
		('FAILED', _('Failed')),
		('SKIPPED', _('Skipped')),
	)

	delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name='notification_logs')
	channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
	status = models.CharField(max_length=20, choices=STATUS_CHOICES)
	target = models.CharField(max_length=255, blank=True)
	message = models.TextField(blank=True)
	error_message = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ('created_at',)

	def __str__(self):
		return f'{self.delivery.invoice.numero} {self.channel} {self.status}'


class InvoiceItem(models.Model):
	invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
	pedido_item = models.ForeignKey(PedidoItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoice_items')
	presentacion = models.ForeignKey(Presentacion, on_delete=models.SET_NULL, null=True, blank=True)
	producto_nombre = models.CharField(max_length=255)
	presentacion_nombre = models.CharField(max_length=120)
	cantidad_facturada = models.PositiveIntegerField(default=1)
	precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
	precio_venta_sugerido_unitario = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
	subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

	class Meta:
		ordering = ('id',)

	def __str__(self):
		return f'{self.producto_nombre} x {self.cantidad_facturada}'


class NotaAjuste(models.Model):
	DOCUMENT_TYPE_CHOICES = (
		('CREDITO', _('Credit note')),
		('DEBITO', _('Debit note')),
	)

	STATUS_CHOICES = (
		('BORRADOR', _('Draft')),
		('APROBADA', _('Approved')),
		('ANULADA', _('Cancelled')),
	)

	REASON_CHOICES = (
		('DAMAGE', _('Physical damage / transport')),
		('DEFECT', _('Factory defect / quality issue')),
		('MISSING_ITEM', _('Missing item')),
		('OTHER', _('Other')),
	)

	CREDIT_TYPE_CHOICES = (
		('CREDIT_DUMP', _('Credit Dump')),
		('CREDIT_RETURN', _('Credit Return')),
	)

	ADJUSTMENT_TYPE_CHOICES = (
		('PRODUCTO', _('Product')),
		('FINANCIERO', _('Financial')),
	)

	INVENTORY_STATUS_CHOICES = (
		('NO_APLICA', _('Not applicable')),
		('PENDIENTE', _('Pending')),
		('PROCESADO', _('Processed')),
		('ANULADO', _('Cancelled')),
	)

	numero = models.CharField(max_length=30, unique=True, blank=True)
	cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name='notas_ajuste')
	invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='notas_ajuste', null=True, blank=True)
	tipo_documento = models.CharField(max_length=20, choices=DOCUMENT_TYPE_CHOICES)
	tipo_ajuste = models.CharField(max_length=20, choices=ADJUSTMENT_TYPE_CHOICES, default='PRODUCTO')
	estado = models.CharField(max_length=20, choices=STATUS_CHOICES, default='BORRADOR')
	motivo = models.CharField(max_length=20, choices=REASON_CHOICES)
	tipo_credito = models.CharField(max_length=20, choices=CREDIT_TYPE_CHOICES, blank=True)
	descripcion = models.TextField(blank=True)
	monto = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	impacto_saldo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	monto_aplicado_invoice = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	monto_aplicado_cliente = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
	inventario_estado = models.CharField(max_length=20, choices=INVENTORY_STATUS_CHOICES, default='NO_APLICA')
	creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='notas_ajuste_creadas')
	aprobada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='notas_ajuste_aprobadas')
	fecha = models.DateTimeField(default=timezone.now)
	creada_en = models.DateTimeField(auto_now_add=True)
	aprobada_en = models.DateTimeField(blank=True, null=True)
	anulada_en = models.DateTimeField(blank=True, null=True)

	class Meta:
		ordering = ('-creada_en',)

	def __str__(self):
		return self.numero or f'Adjustment #{self.pk}'

	def clean(self):
		resolved_cliente = self.cliente
		if resolved_cliente is None and self.invoice_id:
			resolved_cliente = self.invoice.cliente
		if resolved_cliente is None:
			raise ValidationError({'cliente': _('Adjustment notes must be assigned to a customer.')})
		if self.invoice_id and resolved_cliente and self.invoice.cliente_id != resolved_cliente.id:
			raise ValidationError({'invoice': _('The selected invoice does not belong to the selected customer.')})
		if self.tipo_documento == 'CREDITO' and not self.tipo_credito:
			raise ValidationError({'tipo_credito': _('A credit type is required for credit notes.')})
		if self.tipo_documento == 'DEBITO' and self.tipo_credito:
			raise ValidationError({'tipo_credito': _('Debit notes cannot define a credit type.')})

	def save(self, *args, **kwargs):
		is_new = self.pk is None
		if self.cliente_id is None and self.invoice_id:
			self.cliente = self.invoice.cliente
		super().save(*args, **kwargs)
		if is_new and not self.numero:
			prefix = 'CRN' if self.tipo_documento == 'CREDITO' else 'DBN'
			self.numero = f'{prefix}-{timezone.now().year}-{self.pk:06d}'
			super().save(update_fields=['numero'])


class NotaAjusteItem(models.Model):
	nota = models.ForeignKey(NotaAjuste, on_delete=models.CASCADE, related_name='items')
	invoice_item = models.ForeignKey(InvoiceItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='notas_ajuste')
	presentacion = models.ForeignKey(Presentacion, on_delete=models.SET_NULL, null=True, blank=True)
	contenido_fraccionado = models.CharField(max_length=50, blank=True)
	descripcion = models.CharField(max_length=255)
	cantidad = models.PositiveIntegerField(default=1)
	monto_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
	total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

	class Meta:
		ordering = ('id',)

	def __str__(self):
		return f'{self.descripcion} x {self.cantidad}'


class NotaAjusteEvidencePhoto(models.Model):
	nota = models.ForeignKey(NotaAjuste, on_delete=models.CASCADE, related_name='evidence_photos')
	image = models.ImageField(upload_to='invoice-notes/evidence/')
	uploaded_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ('uploaded_at', 'id',)

	def __str__(self):
		return f'{self.nota.numero} evidence {self.pk}'
