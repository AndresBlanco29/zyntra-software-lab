from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from config.clientes.models import Cliente
from config.facturacion.models import Delivery, Invoice
from config.pedidos.crm_pipeline import build_crm_pipeline
from config.pedidos.models import Pedido
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class CrmPipelineTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='bo-crm', password='secret123', role='backoffice')
		self.vendedor = Usuario.objects.create_user(
			username='vendor-crm',
			password='secret123',
			role='vendedor',
			first_name='Ana',
			last_name='Vendedora',
		)
		self.driver = Usuario.objects.create_user(username='driver-crm', password='secret123', role='driver')
		self.cliente_user = Usuario.objects.create_user(username='cliente-crm', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='MI TIERRA LINDA',
			telefono='5550001111',
			direccion='100 Main St',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-CRM-1',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Categoria CRM')
		marca = Marca.objects.create(nombre='Marca CRM')
		producto = Producto.objects.create(nombre='Producto CRM', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('10.00'),
		)

	def _create_pedido(self, *, estado='RECIBIDO', total=Decimal('2500.00'), created_days_ago=0, vendedor=None):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			vendedor=vendedor,
			origen='VENDEDOR' if vendedor else 'CLIENTE',
			estado=estado,
			total=total,
		)
		if created_days_ago:
			created_at = timezone.now() - timedelta(days=created_days_ago)
			Pedido.objects.filter(pk=pedido.pk).update(creada_en=created_at, actualizada_en=created_at)
			pedido.refresh_from_db()
		return pedido

	def _column_cards(self, pipeline, key):
		return next(column for column in pipeline['columns'] if column.key == key).cards

	def test_confirmed_orders_appear_in_first_column_with_vendor_name(self):
		pedido = self._create_pedido(vendedor=self.vendedor)

		pipeline = build_crm_pipeline(period='today')
		cards = self._column_cards(pipeline, 'confirmed')

		self.assertEqual(len(cards), 1)
		self.assertEqual(cards[0].pedido_id, pedido.id)
		self.assertEqual(cards[0].customer_name, 'MI TIERRA LINDA')
		self.assertEqual(cards[0].manager_name, 'Ana Vendedora')
		self.assertEqual(cards[0].total, Decimal('2500.00'))

	def test_unfinished_orders_from_previous_days_remain_visible(self):
		pedido = self._create_pedido(estado='PARA_VERIFICAR', created_days_ago=3)

		pipeline = build_crm_pipeline(period='today')
		cards = self._column_cards(pipeline, 'picking_pending')

		self.assertEqual(len(cards), 1)
		self.assertEqual(cards[0].pedido_id, pedido.id)

	def test_delivered_orders_only_show_for_selected_period(self):
		pedido = self._create_pedido(estado='DESPACHADO', created_days_ago=1)
		yesterday = timezone.localdate() - timedelta(days=1)
		Pedido.objects.filter(pk=pedido.pk).update(actualizada_en=timezone.now() - timedelta(days=1))

		today_pipeline = build_crm_pipeline(period='today', reference_date=timezone.localdate())
		week_pipeline = build_crm_pipeline(period='week', reference_date=timezone.localdate())

		self.assertEqual(len(self._column_cards(today_pipeline, 'delivered')), 0)
		self.assertEqual(len(self._column_cards(week_pipeline, 'delivered')), 1)

	def test_driver_column_contains_route_invoices(self):
		pedido = self._create_pedido(estado='INVOICE_GENERADA')
		invoice = Invoice.objects.create(
			pedido=pedido,
			cliente=self.cliente,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			subtotal=pedido.total,
			total_neto=pedido.total,
			saldo_cliente=pedido.total,
			creada_por=self.backoffice,
		)
		Delivery.objects.create(
			invoice=invoice,
			driver=self.driver,
			delivery_address=self.cliente.direccion,
			delivery_city=self.cliente.ciudad,
			delivery_state=self.cliente.estado,
			delivery_postal_code=self.cliente.codigo_postal,
			delivery_country=self.cliente.pais,
		)

		pipeline = build_crm_pipeline(period='today')
		cards = self._column_cards(pipeline, 'driver')

		self.assertEqual(len(cards), 1)
		self.assertEqual(cards[0].pedido_id, pedido.id)

	def test_column_period_total_sums_only_orders_created_in_period(self):
		self._create_pedido(total=Decimal('100.00'))
		self._create_pedido(total=Decimal('200.00'), created_days_ago=4)

		pipeline = build_crm_pipeline(period='today')
		confirmed_column = next(column for column in pipeline['columns'] if column.key == 'confirmed')

		self.assertEqual(confirmed_column.period_total, Decimal('100.00'))
		self.assertEqual(confirmed_column.card_count, 2)

	def test_search_and_vendor_filters_narrow_results(self):
		other_vendor = Usuario.objects.create_user(username='vendor-2', password='secret123', role='vendedor')
		target = self._create_pedido(vendedor=self.vendedor, total=Decimal('500.00'))
		self._create_pedido(vendedor=other_vendor, total=Decimal('900.00'))

		pipeline = build_crm_pipeline(period='today', vendedor_id=self.vendedor.id, search_term='TIERRA')
		cards = self._column_cards(pipeline, 'confirmed')

		self.assertEqual(len(cards), 1)
		self.assertEqual(cards[0].pedido_id, target.id)
