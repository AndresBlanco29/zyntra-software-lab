from django.db import models


class Testimonio(models.Model):

    nombre = models.CharField(max_length=120)

    negocio = models.CharField(
        max_length=150,
        blank=True,
        null=True
    )

    comentario = models.TextField()

    estrellas = models.IntegerField(
        default=5
    )

    foto = models.ImageField(
        upload_to="testimonios/",
        blank=True,
        null=True
    )

    orden = models.PositiveIntegerField(
        default=0
    )

    activo = models.BooleanField(
        default=True
    )

    creado = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["orden", "-creado"]

    def __str__(self):
        return f"{self.nombre} - {self.negocio}"