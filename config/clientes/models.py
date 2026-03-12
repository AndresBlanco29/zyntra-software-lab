from django.db import models
from usuarios.models import Usuario

class Cliente(models.Model):

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

    aprobado = models.BooleanField(
        default=False
    )

    credit_hold = models.BooleanField(
        default=False
    )

    creado_en = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.nombre_empresa
