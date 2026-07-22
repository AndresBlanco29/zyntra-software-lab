from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from config.clientes.models import Cliente
from config.core.profit import build_order_line_profit
from config.pedidos.models import Pedido, PedidoItem
from config.productos.landed_cost import resolve_effective_cost, resolve_landed_cost_amount
from config.productos.models import (
	Categoria,
	ConfiguracionLandedCost,
	Marca,
	Presentacion,
	Producto,
	Promocion,
	PromocionEscala,
)
from config.productos.promotions import (
	calcular_unidades_regalo_escala,
	sincronizar_regalos_promocion_en_pedido,
)
from config.usuarios.models import Usuario


class LandedCostTests(TestCase):
	def setUp(self):
		categoria = Categoria.objects.create(nombre='Cat LC')
		marca = Marca.objects.create(nombre='Marca LC')
		producto = Producto.objects.create(nombre='Producto LC', categoria=categoria, marca=marca, activo=True)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('10.00'),
			precio_1=Decimal('15.00'),
		)

	def test_global_percent_landed_cost_adds_to_rcost(self):
		config = ConfiguracionLandedCost.obtener()
		config.tipo = ConfiguracionLandedCost.TIPO_PERCENT
		config.valor = Decimal('10.00')
		config.save()

		self.assertEqual(resolve_landed_cost_amount(self.presentacion), Decimal('1.00'))
		self.assertEqual(resolve_effective_cost(self.presentacion), Decimal('11.00'))

	def test_presentation_fixed_override_beats_global(self):
		config = ConfiguracionLandedCost.obtener()
		config.tipo = ConfiguracionLandedCost.TIPO_PERCENT
		config.valor = Decimal('50.00')
		config.save()
		self.presentacion.landed_cost_override_tipo = Presentacion.LANDED_OVERRIDE_FIXED
		self.presentacion.landed_cost_override_valor = Decimal('2.50')
		self.presentacion.save()

		self.assertEqual(resolve_landed_cost_amount(self.presentacion), Decimal('2.50'))
		self.assertEqual(resolve_effective_cost(self.presentacion), Decimal('12.50'))

	def test_profit_uses_effective_cost(self):
		config = ConfiguracionLandedCost.obtener()
		config.tipo = ConfiguracionLandedCost.TIPO_FIXED
		config.valor = Decimal('2.00')
		config.save()
		profit = build_order_line_profit(
			cost=resolve_effective_cost(self.presentacion),
			list_price=Decimal('20.00'),
			quantity=1,
		)
		# sale 20, effective cost 12 => margin 40%
		self.assertEqual(profit['profit_percent'], Decimal('40.00'))


class FreeGiftPromotionTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='bo-gift', password='x', role='backoffice')
		customer = Usuario.objects.create_user(username='cli-gift', password='x', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=customer,
			nombre_empresa='Gift Client',
			telefono='555',
			direccion='A',
			ciudad='Marietta',
			estado='GA',
			codigo_postal='30062',
			pais='USA',
			sales_tax_number='TAX-GIFT',
			certificado_tax=SimpleUploadedFile('cert.txt', b'cert'),
			aprobado=True,
		)
		categoria = Categoria.objects.create(nombre='Cat Gift')
		marca = Marca.objects.create(nombre='Marca Gift')
		producto_a = Producto.objects.create(nombre='Producto A', categoria=categoria, marca=marca, activo=True)
		producto_b = Producto.objects.create(nombre='Producto B', categoria=categoria, marca=marca, activo=True)
		self.presentacion_a = Presentacion.objects.create(
			producto=producto_a, nombre='Caja A', unidades=1, tipo_contenido='caja',
			costo=Decimal('5.00'), precio_1=Decimal('10.00'),
		)
		self.presentacion_b = Presentacion.objects.create(
			producto=producto_b, nombre='Caja B', unidades=1, tipo_contenido='caja',
			costo=Decimal('4.00'), precio_1=Decimal('8.00'),
		)
		self.promo = Promocion.objects.create(
			nombre='Buy A get B free',
			producto=producto_a,
			presentacion=self.presentacion_a,
			activa=True,
		)
		self.escala = PromocionEscala.objects.create(
			promocion=self.promo,
			cantidad_minima=120,
			tipo_beneficio=PromocionEscala.TIPO_FREE_UNITS,
			unidades_gratis=1,
			presentacion_regalo=self.presentacion_b,
		)

	def test_gift_units_scale_with_multiples(self):
		self.assertEqual(calcular_unidades_regalo_escala(self.escala, 119), 0)
		self.assertEqual(calcular_unidades_regalo_escala(self.escala, 120), 1)
		self.assertEqual(calcular_unidades_regalo_escala(self.escala, 240), 2)

	def test_pedido_sync_creates_free_gift_line(self):
		from config.productos.promotions import resolver_escala_para_linea

		self.escala.refresh_from_db()
		self.assertEqual(self.escala.presentacion_regalo_id, self.presentacion_b.id)

		pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='VENDEDOR',
			estado='RECIBIDO',
			total=Decimal('1200.00'),
		)
		item = PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion_a,
			cantidad_solicitada=120,
			cantidad=120,
			precio=Decimal('10.00'),
			subtotal=Decimal('1200.00'),
		)
		promo, escala, monto = resolver_escala_para_linea(
			producto_id=self.presentacion_a.producto_id,
			presentacion_id=self.presentacion_a.id,
			cantidad=120,
			precio_unitario=Decimal('10.00'),
			presentacion=self.presentacion_a,
			cliente=self.cliente,
			lineas_context=[item],
		)
		self.assertIsNotNone(promo)
		self.assertIsNotNone(escala)
		self.assertEqual(escala.presentacion_regalo_id, self.presentacion_b.id)

		sincronizar_regalos_promocion_en_pedido(pedido)
		gift = PedidoItem.objects.get(pedido=pedido, es_regalo=True)
		self.assertEqual(gift.presentacion_id, self.presentacion_b.id)
		self.assertEqual(gift.cantidad, 1)
		self.assertEqual(gift.precio, Decimal('0.00'))
		self.assertEqual(gift.subtotal, Decimal('0.00'))
