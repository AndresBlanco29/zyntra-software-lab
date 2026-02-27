from django.db import models

class Categoria(models.Model):

    nombre = models.CharField(max_length=100)

    def __str__(self):
        return self.nombre
    
class Producto(models.Model):

    nombre = models.CharField(max_length=255)

    descripcion = models.TextField(blank=True, null=True)

    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    imagen = models.ImageField(
        upload_to='productos/',
        blank=True,
        null=True
    )

    activo = models.BooleanField(default=True)

    quickbooks_id = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre
    
class Presentacion(models.Model):

    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name='presentaciones'
    )

    nombre = models.CharField(max_length=100)

    unidades = models.IntegerField()

    precio = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    def __str__(self):
        return f"{self.producto.nombre} - {self.nombre}"