from django.conf import settings
from django.db import models

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion
from config.productos.models import Presentacion


class Pedido(models.Model):

	ESTADO_CHOICES = (
		('RECIBIDO', 'Recibido'),
		('EN_GESTION', 'En gestion'),
		('LISTO_PARA_PICKING', 'Listo para picking'),
		('DESPACHADO', 'Despachado'),
		('CANCELADO', 'Cancelado'),
	)

	ORIGEN_CHOICES = (
		('CLIENTE', 'Cliente'),
		('VENDEDOR', 'Vendedor'),
	)

	cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='pedidos')
	vendedor = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='pedidos_generados',
		limit_choices_to={'role': 'vendedor'},
	)
	cotizacion = models.OneToOneField(
		Cotizacion,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='pedido_generado',
	)
	origen = models.CharField(max_length=20, choices=ORIGEN_CHOICES)
	canal_toma = models.CharField(max_length=20, blank=True, default='')
	estado = models.CharField(max_length=30, choices=ESTADO_CHOICES, default='RECIBIDO')
	nota_cliente = models.TextField(blank=True)
	nota_backoffice = models.TextField(blank=True)
	acepta_terminos = models.BooleanField(default=False)
	acepta_terminos_en = models.DateTimeField(blank=True, null=True)
	total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	creada_en = models.DateTimeField(auto_now_add=True)
	actualizada_en = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ('-creada_en',)
		db_table = 'pedidos_pedidocompra'

	def __str__(self):
		return f"Pedido #{self.id} - {self.cliente.nombre_empresa}"


class PedidoItem(models.Model):

	pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='items')
	presentacion = models.ForeignKey(Presentacion, on_delete=models.CASCADE)
	cantidad = models.PositiveIntegerField(default=1)
	precio = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)

	def __str__(self):
		return f"{self.presentacion.producto.nombre} x {self.cantidad}"
