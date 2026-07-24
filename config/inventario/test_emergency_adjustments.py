from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from config.inventario.availability import availability_snapshot
from config.inventario.models import InventarioMovimiento, StockPresentacion
from config.inventario.services import registrar_ajuste_emergencia, resolver_ajuste_emergencia
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class EmergencyInventoryAdjustmentTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(
			username='bo-emergency',
			password='secret123',
			role='backoffice',
		)
		self.vendor = Usuario.objects.create_user(
			username='vendor-emergency',
			password='secret123',
			role='vendedor',
		)
		categoria = Categoria.objects.create(nombre='Cat Emergency')
		marca = Marca.objects.create(nombre='Marca Emergency')
		producto = Producto.objects.create(
			nombre='Producto Emergency',
			categoria=categoria,
			marca=marca,
			activo=True,
			quickbooks_id='QB-EMERGENCY-1',
		)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='CS',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('10.00'),
			quickbooks_id='QB-PRES-EMERGENCY-1',
		)
		StockPresentacion.objects.create(
			presentacion=self.presentacion,
			stock_fisico=10,
			stock_reservado=0,
			stock_disponible=10,
		)

	def test_emergency_adjustment_does_not_mutate_quick_inventory(self):
		registrar_ajuste_emergencia(
			presentacion=self.presentacion,
			delta_cantidad=5,
			observacion='QB oversold correction',
			creado_por=self.backoffice,
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(stock.stock_fisico, 10)
		snapshot = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(snapshot['active_manual_adjustments'], 5)
		self.assertEqual(snapshot['available'], 15)
		self.assertTrue(snapshot['has_active_adjustments'])

	def test_negative_emergency_adjustment_reduces_available(self):
		registrar_ajuste_emergencia(
			presentacion=self.presentacion,
			delta_cantidad=-3,
			observacion='Temporary hold',
			creado_por=self.backoffice,
		)
		snapshot = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(snapshot['active_manual_adjustments'], -3)
		self.assertEqual(snapshot['available'], 7)

	def test_resolved_adjustment_stops_affecting_available(self):
		movement = registrar_ajuste_emergencia(
			presentacion=self.presentacion,
			delta_cantidad=4,
			observacion='Temporary boost',
			creado_por=self.backoffice,
		)
		resolver_ajuste_emergencia(
			movimiento=movement,
			resuelto_por=self.backoffice,
			observacion_resolucion='QB inventory corrected',
		)
		movement.refresh_from_db()
		self.assertEqual(movement.estado, InventarioMovimiento.ESTADO_RESOLVED)
		self.assertTrue(InventarioMovimiento.objects.filter(pk=movement.pk).exists())
		snapshot = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(snapshot['active_manual_adjustments'], 0)
		self.assertEqual(snapshot['available'], 10)
		self.assertFalse(snapshot['has_active_adjustments'])

	def test_reason_is_required(self):
		with self.assertRaises(ValidationError):
			registrar_ajuste_emergencia(
				presentacion=self.presentacion,
				delta_cantidad=2,
				observacion='   ',
				creado_por=self.backoffice,
			)

	def test_resolution_observation_is_required(self):
		movement = registrar_ajuste_emergencia(
			presentacion=self.presentacion,
			delta_cantidad=2,
			observacion='Need stock now',
			creado_por=self.backoffice,
		)
		with self.assertRaises(ValidationError):
			resolver_ajuste_emergencia(
				movimiento=movement,
				resuelto_por=self.backoffice,
				observacion_resolucion='',
			)

	def test_detail_shows_banner_and_warning_for_active_adjustment(self):
		registrar_ajuste_emergencia(
			presentacion=self.presentacion,
			delta_cantidad=2,
			observacion='Bridge until QB sync',
			creado_por=self.backoffice,
		)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_inventory_detail', args=[self.presentacion.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'This product has active manual adjustments.')
		self.assertContains(response, 'Emergency Inventory Adjustment')
		self.assertContains(response, 'Resolve Adjustment')

	def test_list_shows_warning_icon_for_active_adjustment(self):
		registrar_ajuste_emergencia(
			presentacion=self.presentacion,
			delta_cantidad=1,
			observacion='List warning',
			creado_por=self.backoffice,
		)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_inventory_list'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Temporary inventory adjustment active')
		self.assertContains(response, '⚠')

	def test_vendor_cannot_create_emergency_adjustment_via_post(self):
		self.client.force_login(self.vendor)
		response = self.client.post(
			reverse('backoffice_inventory_detail', args=[self.presentacion.id]),
			{
				'action': 'emergencia',
				'delta_cantidad': '3',
				'observacion': 'Vendor should not create this',
			},
		)
		self.assertIn(response.status_code, {302, 403})
		self.assertFalse(
			InventarioMovimiento.objects.filter(
				presentacion=self.presentacion,
				tipo='AJUSTE_EMERGENCIA',
			).exists()
		)
