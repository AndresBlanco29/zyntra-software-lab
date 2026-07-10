from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from config.clientes.models import Cliente
from config.facturacion.daily_closing import (
	actualizar_revision_item_cierre,
	agregar_invoices_al_cierre,
	crear_cierre_diario,
	invoice_puede_exportarse_a_quickbooks,
	invoices_elegibles_para_cierre,
	liberar_items_cierre,
)
from config.facturacion.models import Invoice
from config.facturacion.services import generar_invoice_desde_picking
from config.inventario.services import registrar_entrada_manual
from config.pedidos.models import Pedido, PedidoItem
from config.pedidos.services import asignar_picking_a_seleccionador, guardar_verificacion_picking
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario
from config.integrations.quickbooks.views import _outbound_pending_querysets


class DailyClosingFlowTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='dc-bo', password='secret123', role='backoffice')
		self.selector = Usuario.objects.create_user(username='dc-sel', password='secret123', role='seleccionador')
		self.customer_user = Usuario.objects.create_user(
			username='dc-cust',
			password='secret123',
			role='cliente',
			email='dc@example.com',
		)
		self.cliente = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Daily Closing Customer',
			telefono='5551112222',
			direccion='1 Closing St',
			ciudad='Marietta',
			estado='GA',
			codigo_postal='30062',
			pais='USA',
			sales_tax_number='TAX-DC',
			certificado_tax=SimpleUploadedFile('certificado.txt', b'certificado'),
			aprobado=True,
		)
		categoria = Categoria.objects.create(nombre='DC Cat')
		marca = Marca.objects.create(nombre='DC Marca')
		producto = Producto.objects.create(nombre='DC Product', categoria=categoria, marca=marca, activo=True)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('5.00'),
			precio_1=Decimal('10.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=50, observacion='DC stock')
		self.pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='BACKOFFICE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('20.00'),
		)
		self.item = PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('10.00'),
			subtotal=Decimal('20.00'),
		)
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='OK',
			nota_resuelta=True,
		)
		self.invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

	def test_invoice_not_in_qb_pending_until_released(self):
		pending_ids = set(_outbound_pending_querysets()['invoices'].values_list('id', flat=True))
		self.assertNotIn(self.invoice.id, pending_ids)
		self.assertFalse(invoice_puede_exportarse_a_quickbooks(self.invoice))

	def test_add_review_release_makes_invoice_exportable(self):
		cierre = crear_cierre_diario(fecha=timezone.localdate(), usuario=self.backoffice)
		self.assertIn(self.invoice, list(invoices_elegibles_para_cierre()))
		created = agregar_invoices_al_cierre(
			cierre=cierre,
			invoice_ids=[self.invoice.id],
			usuario=self.backoffice,
		)
		self.assertEqual(len(created), 1)
		item = cierre.items.get()

		actualizar_revision_item_cierre(
			item=item,
			payload={
				'factura_revisada': True,
				'pago_verificado': True,
				'entrega_confirmada': True,
				'devolucion_detectada': False,
				'credit_memo_requerida': False,
				'credit_memo_ok': False,
				'notas': 'OK for export',
			},
			usuario=self.backoffice,
		)
		item.refresh_from_db()
		self.assertEqual(item.estado, 'LISTA')
		self.assertTrue(item.lista_para_exportar)

		liberar_items_cierre(cierre=cierre, liberar_todas_listas=True, usuario=self.backoffice)
		self.invoice.refresh_from_db()
		self.assertTrue(self.invoice.cierre_liberada)
		self.assertTrue(invoice_puede_exportarse_a_quickbooks(self.invoice))
		pending_ids = set(_outbound_pending_querysets()['invoices'].values_list('id', flat=True))
		self.assertIn(self.invoice.id, pending_ids)

	def test_cannot_release_without_checklist(self):
		cierre = crear_cierre_diario(fecha=timezone.localdate(), usuario=self.backoffice)
		agregar_invoices_al_cierre(cierre=cierre, invoice_ids=[self.invoice.id], usuario=self.backoffice)
		with self.assertRaises(ValidationError):
			liberar_items_cierre(cierre=cierre, liberar_todas_listas=True, usuario=self.backoffice)

	def test_daily_closing_list_view(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_daily_closing_list'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Daily Closing')
