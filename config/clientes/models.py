import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import models

from config.integrations.quickbooks.constants import QUICKBOOKS_SYNC_STATUS_CHOICES, QUICKBOOKS_SYNC_STATUS_PENDING
from config.productos.models import normalize_price_tier
from config.usuarios.models import Usuario

class Cliente(models.Model):

    PRICE_TIER_UNASSIGNED = 0

    REVIEW_STATUS_PENDING = 'PENDIENTE'
    REVIEW_STATUS_APPROVED = 'APROBADO'
    REVIEW_STATUS_REJECTED = 'RECHAZADO'

    REVIEW_STATUS_CHOICES = (
        (REVIEW_STATUS_PENDING, 'Pendiente'),
        (REVIEW_STATUS_APPROVED, 'Aprobado'),
        (REVIEW_STATUS_REJECTED, 'Rechazado'),
    )

    PRICE_TIER_CHOICES = (
        (PRICE_TIER_UNASSIGNED, 'Sin precios'),
        (1, 'Precio 1'),
        (2, 'Precio 2'),
        (3, 'Precio 3'),
        (4, 'Precio 4'),
        (5, 'Precio 5'),
    )

    PAYMENT_TERMS_PREPAY = 'PREPAY'
    PAYMENT_TERMS_COD = 'COD'
    PAYMENT_TERMS_NET7 = 'NET7'
    PAYMENT_TERMS_NET14 = 'NET14'
    PAYMENT_TERMS_NET21 = 'NET21'

    PAYMENT_TERMS_CHOICES = (
        (PAYMENT_TERMS_PREPAY, 'prepay'),
        (PAYMENT_TERMS_COD, 'COD'),
        (PAYMENT_TERMS_NET7, 'NET7'),
        (PAYMENT_TERMS_NET14, 'NET14'),
        (PAYMENT_TERMS_NET21, 'NET21'),
    )

    PAYMENT_TERMS_DUE_DAYS = {
        PAYMENT_TERMS_PREPAY: 0,
        PAYMENT_TERMS_COD: 0,
        PAYMENT_TERMS_NET7: 7,
        PAYMENT_TERMS_NET14: 14,
        PAYMENT_TERMS_NET21: 21,
    }

    usuario = models.OneToOneField(
        Usuario,
        on_delete=models.CASCADE,
        related_name='cliente'
    )

    nombre_empresa = models.CharField(
        max_length=255
    )

    telefono = models.CharField(
        max_length=20,
        blank=False,
        null=False
    )

    direccion = models.CharField(
        max_length=255,
        blank=False,
        null=False
    )

    ciudad = models.CharField(
        max_length=100,
        blank=False,
        null=False
    )

    estado = models.CharField(
        max_length=100,
        blank=False,
        null=False
    )

    codigo_postal = models.CharField(
    max_length=20,
    blank=True,
    null=True
    )

    pais = models.CharField(
        max_length=100,
        default="USA"
    )

    sales_tax_number = models.CharField(
        max_length=100,
        blank=False,
        null=False
    )

    certificado_tax = models.FileField(
        upload_to='certificados/',
        blank=False,
        null=False
    )

    declaracion_fiscal_aceptada = models.BooleanField(
        default=False
    )

    declaracion_fiscal_aceptada_en = models.DateTimeField(
        blank=True,
        null=True
    )

    aprobado = models.BooleanField(
        default=False
    )

    nivel_precio = models.PositiveSmallIntegerField(
        choices=PRICE_TIER_CHOICES,
        blank=True,
        default=PRICE_TIER_UNASSIGNED,
    )

    estado_revision = models.CharField(
        max_length=20,
        choices=REVIEW_STATUS_CHOICES,
        default=REVIEW_STATUS_PENDING,
        db_index=True,
    )

    nota_rechazo = models.TextField(
        blank=True,
        default=''
    )

    adjunto_rechazo = models.FileField(
        upload_to='certificados/rechazos/',
        blank=True,
        null=True
    )

    rechazado_en = models.DateTimeField(
        blank=True,
        null=True
    )

    rechazado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='clientes_rechazados_admin'
    )

    aprobado_en = models.DateTimeField(
        blank=True,
        null=True
    )

    aprobado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='clientes_aprobados_admin'
    )

    correction_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False
    )

    correction_requested_at = models.DateTimeField(
        blank=True,
        null=True
    )

    corrected_at = models.DateTimeField(
        blank=True,
        null=True
    )

    credit_hold = models.BooleanField(
        default=False
    )

    credit_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        help_text='Maximum total due balance allowed for this customer. Leave empty for no limit.',
    )

    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=(
            'QuickBooks A/R balance: positive = customer owes La Tortilla (due balance / debit); '
            'negative = credit in favor of the customer (credit note balance).'
        ),
    )

    terminos_pago = models.CharField(
        max_length=10,
        choices=PAYMENT_TERMS_CHOICES,
        blank=True,
        default='',
    )

    quickbooks_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        db_index=True,
    )

    sync_status = models.CharField(
        max_length=20,
        choices=QUICKBOOKS_SYNC_STATUS_CHOICES,
        default=QUICKBOOKS_SYNC_STATUS_PENDING,
        db_index=True,
    )

    last_synced_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    creado_en = models.DateTimeField(
        auto_now_add=True
    )

    vendedor_asignado = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clientes_asignados',
        limit_choices_to={'role': 'vendedor'},
    )

    vendedor_asignado_en = models.DateTimeField(
        blank=True,
        null=True,
    )

    vendedor_asignado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='clientes_asignados_por',
    )

    def get_nivel_precio_normalizado(self):
        return normalize_price_tier(self.nivel_precio, default=None)

    def has_assigned_price_tier(self):
        return self.get_nivel_precio_normalizado() is not None

    def get_terminos_pago_label(self):
        if not self.terminos_pago:
            return ''
        return dict(self.PAYMENT_TERMS_CHOICES).get(self.terminos_pago, self.terminos_pago)

    def get_payment_terms_due_days(self):
        if not self.terminos_pago:
            return None
        return self.PAYMENT_TERMS_DUE_DAYS.get(self.terminos_pago)

    def get_payment_due_date(self, base_date):
        due_days = self.get_payment_terms_due_days()
        if due_days is None:
            return None
        return base_date + timedelta(days=due_days)

    @property
    def due_balance(self):
        """Amount the customer owes La Tortilla (QuickBooks positive balance)."""
        return self.balance if self.balance > 0 else Decimal('0.00')

    @property
    def total_amount_owed(self):
        from config.facturacion.services import resolve_customer_amount_owed

        return resolve_customer_amount_owed(cliente=self)

    @property
    def customer_credit_balance(self):
        """Credit in favor of the customer (QuickBooks negative balance, shown as a positive amount)."""
        return abs(self.balance) if self.balance < 0 else Decimal('0.00')

    @property
    def available_credit(self):
        """Unapplied store credit available to apply on invoices."""
        return self.customer_credit_balance

    def get_credit_limit_remaining(self):
        if self.credit_limit is None:
            return None
        return max(Decimal(str(self.credit_limit)) - self.due_balance, Decimal('0.00'))

    def __str__(self):
        return self.nombre_empresa


class ClienteCreditoLimiteAlerta(models.Model):
    ESTADO_PENDIENTE = 'PENDIENTE'
    ESTADO_LIBERADO = 'LIBERADO'
    ESTADO_BLOQUEADO = 'BLOQUEADO'

    ESTADO_CHOICES = (
        (ESTADO_PENDIENTE, 'Pending review'),
        (ESTADO_LIBERADO, 'Released'),
        (ESTADO_BLOQUEADO, 'Blocked'),
    )

    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name='alertas_limite_credito',
    )
    pedido = models.ForeignKey(
        'pedidos.Pedido',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='alertas_limite_credito',
    )
    monto_adeudado = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    monto_operacion = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    limite_credito = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    exceso = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_PENDIENTE, db_index=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    resuelto_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='alertas_limite_credito_resueltas',
    )
    resuelto_en = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ('-creado_en',)

    def __str__(self):
        return f'Credit limit alert #{self.id} - {self.cliente.nombre_empresa}'

    @property
    def saldo_proyectado(self):
        total = Decimal(str(self.monto_adeudado or '0.00')) + Decimal(str(self.monto_operacion or '0.00'))
        return total.quantize(Decimal('0.01'))
