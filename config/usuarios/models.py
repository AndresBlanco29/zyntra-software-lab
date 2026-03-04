from django.contrib.auth.models import AbstractUser
from django.db import models

class Usuario(AbstractUser):

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
        return self.username