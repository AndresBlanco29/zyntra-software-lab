from django.contrib.auth.models import AbstractUser
from django.db import models

class Usuario(AbstractUser):

    ROLE_CHOICES = (
        ('ADMIN', 'Administrador'),
        ('CLIENTE', 'Cliente'),
        ('VENDEDOR', 'Vendedor'),
        ('BACKOFFICE', 'BackOffice'),
        ('SELECCIONADOR', 'Seleccionador'),
        ('DRIVER', 'Driver'),
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='CLIENTE'
    )

    telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True
    )

    documento = models.CharField(
        max_length=50,
        blank=True,
        null=True
    )

    quickbooks_id = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )

    creado_en = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"{self.username} - {self.role}"