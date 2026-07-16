from decimal import Decimal
from datetime import timedelta

from django.test import TestCase, Client as DjangoClient
from django.urls import reverse
from django.utils import timezone

from config.clientes.models import Cliente
from config.cotizaciones.models import CotizacionItem
from config.productos.models import Categoria, Marca, Presentacion, Producto, Promocion
from config.productos.promotions import (
    adjuntar_promociones_a_productos,
    aplicar_promocion_en_item_sesion,
    promociones_activas_queryset,
    resolver_promocion_para_linea,
)
from config.usuarios.models import Usuario


class PromocionResolverTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Promo Cat')
        self.marca = Marca.objects.create(nombre='Promo Brand')
        self.producto = Producto.objects.create(
            nombre='Promo Product',
            categoria=self.categoria,
            marca=self.marca,
            activo=True,
        )
        self.presentacion = Presentacion.objects.create(
            producto=self.producto,
            nombre='Case',
            unidades=12,
            tipo_contenido='unidad',
            precio_1=Decimal('20.00'),
            precio_2=Decimal('20.00'),
            precio_3=Decimal('20.00'),
            precio_4=Decimal('20.00'),
            precio_5=Decimal('20.00'),
        )
        Presentacion.objects.filter(id=self.presentacion.id).update(
            precio_1=Decimal('20.00'),
            precio_2=Decimal('20.00'),
            precio_3=Decimal('20.00'),
            precio_4=Decimal('20.00'),
            precio_5=Decimal('20.00'),
        )
        self.presentacion.refresh_from_db()

    def test_inactive_or_out_of_range_not_active(self):
        now = timezone.now()
        past = Promocion.objects.create(
            nombre='Past',
            producto=self.producto,
            cantidad_minima=1,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('10'),
            fecha_fin=now - timedelta(days=1),
            activa=True,
        )
        future = Promocion.objects.create(
            nombre='Future',
            producto=self.producto,
            cantidad_minima=1,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('10'),
            fecha_inicio=now + timedelta(days=1),
            activa=True,
        )
        inactive = Promocion.objects.create(
            nombre='Off',
            producto=self.producto,
            cantidad_minima=1,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('10'),
            activa=False,
        )
        active_ids = set(promociones_activas_queryset(now=now).values_list('id', flat=True))
        self.assertNotIn(past.id, active_ids)
        self.assertNotIn(future.id, active_ids)
        self.assertNotIn(inactive.id, active_ids)

    def test_percent_and_fixed_threshold(self):
        Promocion.objects.create(
            nombre='15 percent at 10',
            descripcion='Buy 10 get 15%',
            producto=self.producto,
            cantidad_minima=10,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('15'),
            activa=True,
        )
        promo, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id,
            presentacion_id=self.presentacion.id,
            cantidad=9,
            precio_unitario=Decimal('20.00'),
        )
        self.assertIsNone(promo)
        self.assertEqual(monto, Decimal('0.00'))

        promo, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id,
            presentacion_id=self.presentacion.id,
            cantidad=10,
            precio_unitario=Decimal('20.00'),
        )
        self.assertIsNotNone(promo)
        self.assertEqual(monto, Decimal('3.00'))

        Promocion.objects.all().delete()
        Promocion.objects.create(
            nombre='2 dollars at 5',
            producto=self.producto,
            cantidad_minima=5,
            tipo_beneficio=Promocion.TIPO_FIXED,
            valor_beneficio=Decimal('2.00'),
            activa=True,
        )
        promo, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id,
            presentacion_id=self.presentacion.id,
            cantidad=5,
            precio_unitario=Decimal('20.00'),
        )
        self.assertEqual(monto, Decimal('2.00'))

    def test_chooses_greatest_per_unit_savings(self):
        Promocion.objects.create(
            nombre='10 percent',
            producto=self.producto,
            cantidad_minima=1,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('10'),
            activa=True,
        )
        Promocion.objects.create(
            nombre='3 dollars',
            producto=self.producto,
            cantidad_minima=1,
            tipo_beneficio=Promocion.TIPO_FIXED,
            valor_beneficio=Decimal('3.00'),
            activa=True,
        )
        promo, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id,
            presentacion_id=self.presentacion.id,
            cantidad=1,
            precio_unitario=Decimal('20.00'),
        )
        self.assertEqual(promo.nombre, '3 dollars')
        self.assertEqual(monto, Decimal('3.00'))

    def test_catalog_marks_products_with_active_promo(self):
        Promocion.objects.create(
            nombre='Catalog promo',
            descripcion='Special deal',
            producto=self.producto,
            cantidad_minima=1,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('10'),
            activa=True,
        )
        productos = adjuntar_promociones_a_productos([self.producto])
        self.assertIsNotNone(productos[0].promocion_activa)
        self.assertEqual(productos[0].promocion_texto, 'Special deal')

    def test_session_item_clears_promo_below_threshold(self):
        Promocion.objects.create(
            nombre='Threshold',
            producto=self.producto,
            cantidad_minima=10,
            tipo_beneficio=Promocion.TIPO_FIXED,
            valor_beneficio=Decimal('1.50'),
            activa=True,
        )
        item = {
            'producto_id': self.producto.id,
            'presentacion_id': self.presentacion.id,
            'cantidad': 10,
            'precio': 20,
        }
        aplicar_promocion_en_item_sesion(item)
        self.assertTrue(item['descuento_aplicado'])
        self.assertEqual(float(item['descuento_monto']), 1.5)
        self.assertEqual(item['descuento_origen'], 'promocion')

        item['cantidad'] = 3
        aplicar_promocion_en_item_sesion(item)
        self.assertFalse(item['descuento_aplicado'])
        self.assertEqual(item.get('descuento_origen'), '')


class PromocionPersistenceTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Promo Persist Cat')
        self.marca = Marca.objects.create(nombre='Promo Persist Brand')
        self.producto = Producto.objects.create(
            nombre='Persist Product',
            categoria=self.categoria,
            marca=self.marca,
            activo=True,
        )
        self.presentacion = Presentacion.objects.create(
            producto=self.producto,
            nombre='Case',
            unidades=12,
            tipo_contenido='unidad',
            precio_1=Decimal('10.00'),
            precio_2=Decimal('10.00'),
            precio_3=Decimal('10.00'),
            precio_4=Decimal('10.00'),
            precio_5=Decimal('10.00'),
        )
        Presentacion.objects.filter(id=self.presentacion.id).update(
            precio_1=Decimal('10.00'),
            precio_2=Decimal('10.00'),
            precio_3=Decimal('10.00'),
            precio_4=Decimal('10.00'),
            precio_5=Decimal('10.00'),
            costo=None,
        )
        self.presentacion.refresh_from_db()
        Promocion.objects.create(
            nombre='Persist 20%',
            descripcion='Buy 5 get 20%',
            producto=self.producto,
            cantidad_minima=5,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('20'),
            activa=True,
        )
        self.user = Usuario.objects.create_user(
            username='promo-client',
            password='secret123',
            role='cliente',
        )
        self.cliente = Cliente.objects.create(
            usuario=self.user,
            nombre_empresa='Promo Client Co',
            telefono='5550009999',
            direccion='100 Promo St',
            ciudad='Atlanta',
            estado='GA',
            codigo_postal='30301',
            pais='USA',
            sales_tax_number='TX-PROMO-1',
            certificado_tax='certificados/test.pdf',
            nivel_precio=1,
            estado_revision=Cliente.REVIEW_STATUS_APPROVED,
            aprobado=True,
        )

    def test_guardar_cotizacion_applies_descuento_monto(self):
        client = DjangoClient()
        client.force_login(self.user)
        session = client.session
        session['carrito'] = {
            str(self.presentacion.id): {
                'producto_id': self.producto.id,
                'presentacion_id': self.presentacion.id,
                'nombre': self.producto.nombre,
                'cantidad': 5,
                'precio': 10.0,
            }
        }
        session.save()

        response = client.post(reverse('guardar_cotizacion'), {'nota': 'promo test'})
        self.assertEqual(response.status_code, 302)

        item = CotizacionItem.objects.get(presentacion=self.presentacion)
        self.assertTrue(item.descuento_aplicado)
        self.assertEqual(item.descuento_monto, Decimal('2.00'))
        self.assertEqual(item.subtotal, Decimal('40.00'))

    def test_catalogo_shows_promotion_badge(self):
        client = DjangoClient()
        response = client.get(reverse('catalogo'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Promotion')
        self.assertContains(response, 'Buy 5 get 20%')
