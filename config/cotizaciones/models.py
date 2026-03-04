from django.db import models
from clientes.models import Cliente
from productos.models import Producto, Presentacion
from usuarios.models import Usuario


class Cotizacion(models.Model):

    ESTADO_CHOICES = (
        ('BORRADOR', 'Borrador'),
        ('ENVIADA', 'Enviada'),
        ('APROBADA', "Aprobada"),
        ('RECHAZADA', 'Rechazada'),
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
        max_length=20,
        choices=ESTADO_CHOICES,
        default='BORRADOR'
    )

    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

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

    def __str__(self):
        return f"{self.presentacion.producto.nombre} x {self.cantidad}"