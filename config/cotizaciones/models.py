import uuid
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from config.clientes.models import Cliente
from config.productos.models import Presentacion
from config.usuarios.models import Usuario


class Cotizacion(models.Model):

    ESTADO_CHOICES = (
        ('BORRADOR', _('Draft')),
        ('ENVIADA', _('Sent by client')),
        ('LISTA_PARA_CONFIRMACION', _('Ready for confirmation')),
        ('CONFIRMADA_CLIENTE', _('Confirmed by client')),
        ('CANCELADA_CLIENTE', _('Canceled by client')),
        ('APROBADA', _('Approved')),
        ('RECHAZADA', _('Rejected')),
    )

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)

    vendedor = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'VENDEDOR'},
        null=True,
        blank=True
    )

    fecha = models.DateTimeField(auto_now_add=True)

    estado = models.CharField(
        max_length=30,
        choices=ESTADO_CHOICES,
        default='BORRADOR'
    )

    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    nota_cliente = models.TextField(blank=True)
    nota_confirmacion_cliente = models.TextField(blank=True)
    nota_backoffice = models.TextField(blank=True)
    backoffice_pricing_confirmed = models.BooleanField(default=False)
    token_cliente = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    correo_enviado = models.BooleanField(default=False)
    correo_enviado_en = models.DateTimeField(null=True, blank=True)
    sms_enviado = models.BooleanField(default=False)
    sms_enviado_en = models.DateTimeField(null=True, blank=True)
    whatsapp_enviado = models.BooleanField(default=False)
    whatsapp_enviado_en = models.DateTimeField(null=True, blank=True)
    whatsapp_manual_abierto = models.BooleanField(default=False)
    whatsapp_manual_abierto_en = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Cotizacion #{self.id} - {self.cliente}"


class CotizacionItem(models.Model):

    cotizacion = models.ForeignKey(
        Cotizacion,
        on_delete=models.CASCADE,
        related_name='items'
    )

    presentacion = models.ForeignKey(
        Presentacion,
        on_delete=models.CASCADE
    )

    cantidad = models.IntegerField()

    precio = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )
    descuento_aplicado = models.BooleanField(default=False)
    descuento_monto = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.presentacion.producto.nombre} x {self.cantidad}"

    @property
    def precio_unitario_neto(self):
        from config.pedidos.services import calcular_precio_unitario_neto_item

        return calcular_precio_unitario_neto_item(
            precio=self.precio,
            descuento_aplicado=self.descuento_aplicado,
            descuento_monto=self.descuento_monto,
        )

    @property
    def descuento_linea_total(self):
        if not self.descuento_aplicado:
            return Decimal('0.00')
        return (self.descuento_monto or Decimal('0.00')) * Decimal(str(self.cantidad or 0))
