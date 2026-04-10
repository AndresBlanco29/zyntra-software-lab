from django.contrib.auth.models import AbstractUser
from django.db import models

class Usuario(AbstractUser):

    ROLE_CHOICES = (
        ('admin', 'Administrador'),
        ('vendedor', 'Vendedor'),
        ('backoffice', 'BackOffice'),
        ('cliente', 'Cliente'),
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='cliente'
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

    permission_overrides = models.JSONField(
        default=dict,
        blank=True
    )

    creado_en = models.DateTimeField(
        auto_now_add=True
    )

    def normalized_permission_overrides(self):
        from .permissions import normalize_permission_overrides

        return normalize_permission_overrides(self.permission_overrides)

    def has_internal_permission(self, permission_code):
        from .permissions import user_has_permission

        return user_has_permission(self, permission_code)

    def get_permission_summary_labels(self):
        from .permissions import get_permission_summary_labels

        return get_permission_summary_labels(self)

    def __str__(self):
        return self.username
    
