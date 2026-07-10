from django.conf import settings
from django.db import models

from config.clientes.models import Cliente


class TakeOrderDraft(models.Model):
	"""Persistent Take Order cart so a page reload does not lose line items."""

	vendedor = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name='take_order_drafts',
	)
	cliente = models.ForeignKey(
		Cliente,
		on_delete=models.CASCADE,
		related_name='take_order_drafts',
	)
	cart_data = models.JSONField(default=dict, blank=True)
	actualizada_en = models.DateTimeField(auto_now=True)
	creada_en = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=('vendedor', 'cliente'),
				name='vendedores_takeorderdraft_vendedor_cliente_uniq',
			),
		]
		ordering = ('-actualizada_en',)

	def __str__(self):
		return f"Draft {self.vendedor_id} / cliente {self.cliente_id} ({len(self.cart_data or {})} lines)"

	@property
	def line_count(self):
		return len(self.cart_data or {})

	@property
	def item_quantity_total(self):
		return sum(int(item.get('cantidad') or 0) for item in (self.cart_data or {}).values())
