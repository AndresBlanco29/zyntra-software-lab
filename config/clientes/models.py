import uuid

from django.db import models
from config.usuarios.models import Usuario

class Cliente(models.Model):

    REVIEW_STATUS_PENDING = 'PENDIENTE'
    REVIEW_STATUS_APPROVED = 'APROBADO'
    REVIEW_STATUS_REJECTED = 'RECHAZADO'

    REVIEW_STATUS_CHOICES = (
        (REVIEW_STATUS_PENDING, 'Pendiente'),
        (REVIEW_STATUS_APPROVED, 'Aprobado'),
        (REVIEW_STATUS_REJECTED, 'Rechazado'),
    )

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

    creado_en = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.nombre_empresa
