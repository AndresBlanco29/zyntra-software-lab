from decimal import Decimal
from datetime import timedelta

from django.test import TestCase, Client as DjangoClient
from django.urls import reverse
from django.utils import timezone

from config.clientes.models import Cliente, TipoCliente
from config.cotizaciones.models import CotizacionItem
from config.productos.models import Categoria, Marca, Presentacion, Producto, Promocion, PromocionEscala, PromocionProducto
from config.productos.promotions import (
    adjuntar_promociones_a_productos,
    aplicar_promocion_en_item_sesion,
    combos_para_catalogo,
    estado_promocion_para_linea,
    promociones_activas_queryset,
    reaplicar_promociones_en_lineas_sesion,
    resolver_promocion_para_linea,
)
from config.usuarios.models import Usuario


def _crear_escala(promocion, **kwargs):
    kwargs.setdefault('cantidad_minima', 1)
    kwargs.setdefault('tipo_beneficio', PromocionEscala.TIPO_PERCENT)
    return PromocionEscala.objects.create(promocion=promocion, **kwargs)


class PromocionEscalaModelTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Escala Cat')
        self.marca = Marca.objects.create(nombre='Escala Brand')
        self.producto = Producto.objects.create(
            nombre='Escala Product', categoria=self.categoria, marca=self.marca, activo=True,
        )
        self.promocion = Promocion.objects.create(nombre='Escala Promo', producto=self.producto, activa=True)

    def test_percent_escala_requires_value_within_range(self):
        escala = PromocionEscala(
            promocion=self.promocion, cantidad_minima=10,
            tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('150'),
        )
        with self.assertRaises(Exception):
            escala.full_clean()

    def test_free_units_escala_requires_unidades_gratis(self):
        escala = PromocionEscala(
            promocion=self.promocion, cantidad_minima=10,
            tipo_beneficio=PromocionEscala.TIPO_FREE_UNITS,
        )
        with self.assertRaises(Exception):
            escala.full_clean()

    def test_free_units_escala_clears_valor_beneficio(self):
        escala = PromocionEscala(
            promocion=self.promocion, cantidad_minima=10,
            tipo_beneficio=PromocionEscala.TIPO_FREE_UNITS, unidades_gratis=1,
        )
        escala.full_clean()
        self.assertIsNone(escala.valor_beneficio)

    def test_multiple_escalas_on_same_promotion(self):
        _crear_escala(self.promocion, cantidad_minima=12, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('5'))
        _crear_escala(self.promocion, cantidad_minima=24, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('10'))
        _crear_escala(self.promocion, cantidad_minima=48, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('15'))
        self.assertEqual(self.promocion.escalas.count(), 3)
        self.assertEqual(self.promocion.escala_minima.cantidad_minima, 12)

    def test_duplicate_cantidad_minima_rejected(self):
        _crear_escala(self.promocion, cantidad_minima=10, valor_beneficio=Decimal('5'))
        dup = PromocionEscala(
            promocion=self.promocion, cantidad_minima=10,
            tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('7'),
        )
        # The uniqueness of (promocion, cantidad_minima) is enforced by a
        # Meta UniqueConstraint, which Django validates via validate_constraints()
        # (validate_unique() only covers unique=True / unique_together).
        with self.assertRaises(Exception):
            dup.validate_constraints()


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
        past = Promocion.objects.create(nombre='Past', producto=self.producto, fecha_fin=now - timedelta(days=1), activa=True)
        _crear_escala(past, valor_beneficio=Decimal('10'))
        future = Promocion.objects.create(nombre='Future', producto=self.producto, fecha_inicio=now + timedelta(days=1), activa=True)
        _crear_escala(future, valor_beneficio=Decimal('10'))
        inactive = Promocion.objects.create(nombre='Off', producto=self.producto, activa=False)
        _crear_escala(inactive, valor_beneficio=Decimal('10'))

        active_ids = set(promociones_activas_queryset(now=now).values_list('id', flat=True))
        self.assertNotIn(past.id, active_ids)
        self.assertNotIn(future.id, active_ids)
        self.assertNotIn(inactive.id, active_ids)

    def test_percent_and_fixed_threshold(self):
        promo = Promocion.objects.create(nombre='15 percent at 10', descripcion='Buy 10 get 15%', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('15'))

        promo_result, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id,
            cantidad=9, precio_unitario=Decimal('20.00'),
        )
        self.assertIsNone(promo_result)
        self.assertEqual(monto, Decimal('0.00'))

        promo_result, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id,
            cantidad=10, precio_unitario=Decimal('20.00'),
        )
        self.assertIsNotNone(promo_result)
        self.assertEqual(monto, Decimal('3.00'))

        Promocion.objects.all().delete()
        promo2 = Promocion.objects.create(nombre='2 dollars at 5', producto=self.producto, activa=True)
        _crear_escala(promo2, cantidad_minima=5, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('2.00'))
        promo_result, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id,
            cantidad=5, precio_unitario=Decimal('20.00'),
        )
        self.assertEqual(monto, Decimal('2.00'))

    def test_multiple_scales_pick_best_qualifying_tier(self):
        promo = Promocion.objects.create(nombre='Tiered', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=12, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('5'))
        _crear_escala(promo, cantidad_minima=24, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('10'))
        _crear_escala(promo, cantidad_minima=48, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('15'))

        _, monto_12 = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id, cantidad=12, precio_unitario=Decimal('20.00'),
        )
        self.assertEqual(monto_12, Decimal('1.00'))

        _, monto_30 = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id, cantidad=30, precio_unitario=Decimal('20.00'),
        )
        self.assertEqual(monto_30, Decimal('2.00'))

        _, monto_48 = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id, cantidad=48, precio_unitario=Decimal('20.00'),
        )
        self.assertEqual(monto_48, Decimal('3.00'))

    def test_free_units_escala_computes_equivalent_discount(self):
        promo = Promocion.objects.create(nombre='Free units', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_FREE_UNITS, unidades_gratis=1)

        _, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id, cantidad=10, precio_unitario=Decimal('20.00'),
        )
        # 1 free unit worth $20 spread across the 10 purchased units = $2.00/unit.
        self.assertEqual(monto, Decimal('2.00'))

    def test_precio_especial_escala_discounts_down_to_special_price(self):
        promo = Promocion.objects.create(nombre='Special price', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=5, tipo_beneficio=PromocionEscala.TIPO_PRECIO_ESPECIAL, valor_beneficio=Decimal('15.00'))

        _, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id, cantidad=5, precio_unitario=Decimal('20.00'),
        )
        self.assertEqual(monto, Decimal('5.00'))

    def test_chooses_greatest_per_unit_savings_across_promotions(self):
        promo1 = Promocion.objects.create(nombre='10 percent', producto=self.producto, activa=True)
        _crear_escala(promo1, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('10'))
        promo2 = Promocion.objects.create(nombre='3 dollars', producto=self.producto, activa=True)
        _crear_escala(promo2, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('3.00'))

        promo_result, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id, cantidad=1, precio_unitario=Decimal('20.00'),
        )
        self.assertEqual(promo_result.nombre, '3 dollars')
        self.assertEqual(monto, Decimal('3.00'))

    def test_catalog_marks_products_with_active_promo(self):
        promo = Promocion.objects.create(nombre='Catalog promo', descripcion='Special deal', producto=self.producto, activa=True)
        _crear_escala(promo, valor_beneficio=Decimal('10'))

        productos = adjuntar_promociones_a_productos([self.producto])
        self.assertIsNotNone(productos[0].promocion_activa)
        self.assertEqual(productos[0].promocion_texto, 'Special deal')
        self.assertEqual(productos[0].promocion_cantidad_minima, 1)
        self.assertEqual(len(productos[0].promocion_escalas), 1)

    def test_catalog_attaches_all_promotion_scales(self):
        promo = Promocion.objects.create(nombre='Tiered catalog promo', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('0.25'))
        _crear_escala(promo, cantidad_minima=20, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('0.50'))

        productos = adjuntar_promociones_a_productos([self.producto])
        escalas = productos[0].promocion_escalas
        self.assertEqual(len(escalas), 2)
        self.assertEqual(escalas[0].cantidad_minima, 10)
        self.assertEqual(escalas[1].cantidad_minima, 20)

    def test_session_item_clears_promo_below_threshold(self):
        promo = Promocion.objects.create(nombre='Threshold', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('1.50'))

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

        state = estado_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id,
            cantidad=3, precio_unitario=Decimal('20.00'),
        )
        self.assertTrue(state['available'])
        self.assertFalse(state['applied'])
        self.assertEqual(state['minimum'], 10)
        self.assertEqual(state['missing'], 7)


class PromocionTipoClienteTests(TestCase):
    """Promotions can be scoped to customer types (e.g. Supermarkets vs Distributors)."""

    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Tipo Cliente Cat')
        self.marca = Marca.objects.create(nombre='Tipo Cliente Brand')
        self.producto = Producto.objects.create(nombre='Tipo Cliente Product', categoria=self.categoria, marca=self.marca, activo=True)
        self.presentacion = Presentacion.objects.create(
            producto=self.producto, nombre='Case', unidades=12, tipo_contenido='unidad',
        )
        Presentacion.objects.filter(id=self.presentacion.id).update(precio_1=Decimal('20.00'))
        self.presentacion.refresh_from_db()

        self.supermercados = TipoCliente.objects.create(codigo='supermercados-test', nombre='Supermarkets')
        self.distribuidores = TipoCliente.objects.create(codigo='distribuidores-test', nombre='Distributors')

        self.promo = Promocion.objects.create(nombre='Supermarket only', producto=self.producto, activa=True)
        self.promo.tipos_cliente.add(self.supermercados)
        _crear_escala(self.promo, cantidad_minima=1, valor_beneficio=Decimal('10'))

        self.cliente_super = self._crear_cliente('super-client', tipo_cliente=self.supermercados)
        self.cliente_dist = self._crear_cliente('dist-client', tipo_cliente=self.distribuidores)
        self.cliente_sin_tipo = self._crear_cliente('no-type-client', tipo_cliente=None)

    def _crear_cliente(self, username, *, tipo_cliente):
        usuario = Usuario.objects.create_user(username=username, password='secret123', role='cliente')
        return Cliente.objects.create(
            usuario=usuario, nombre_empresa=f'{username} Co', telefono='5551234567',
            direccion='1 Test St', ciudad='Atlanta', estado='GA', codigo_postal='30301', pais='USA',
            sales_tax_number='TX-1', certificado_tax='certificados/test.pdf',
            nivel_precio=1, estado_revision=Cliente.REVIEW_STATUS_APPROVED, aprobado=True,
            tipo_cliente=tipo_cliente,
        )

    def test_promotion_without_tipos_cliente_applies_to_everyone(self):
        Promocion.objects.all().delete()
        promo = Promocion.objects.create(nombre='Open to all', producto=self.producto, activa=True)
        _crear_escala(promo, valor_beneficio=Decimal('5'))

        for cliente in (self.cliente_super, self.cliente_dist, self.cliente_sin_tipo, None):
            _, monto = resolver_promocion_para_linea(
                producto_id=self.producto.id, presentacion_id=self.presentacion.id,
                cantidad=1, precio_unitario=Decimal('20.00'), cliente=cliente,
            )
            self.assertEqual(monto, Decimal('1.00'))

    def test_promotion_scoped_to_supermarkets_only_matches_that_type(self):
        promo_result, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id,
            cantidad=1, precio_unitario=Decimal('20.00'), cliente=self.cliente_super,
        )
        self.assertIsNotNone(promo_result)
        self.assertEqual(monto, Decimal('2.00'))

        promo_result, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id,
            cantidad=1, precio_unitario=Decimal('20.00'), cliente=self.cliente_dist,
        )
        self.assertIsNone(promo_result)
        self.assertEqual(monto, Decimal('0.00'))

    def test_client_without_type_does_not_see_restricted_promotions(self):
        promo_result, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id,
            cantidad=1, precio_unitario=Decimal('20.00'), cliente=self.cliente_sin_tipo,
        )
        self.assertIsNone(promo_result)
        self.assertEqual(monto, Decimal('0.00'))

    def test_no_cliente_context_does_not_filter_by_type(self):
        """Internal/BackOffice contexts without a shopping customer see every promo."""
        promo_result, monto = resolver_promocion_para_linea(
            producto_id=self.producto.id, presentacion_id=self.presentacion.id,
            cantidad=1, precio_unitario=Decimal('20.00'), cliente=None,
        )
        self.assertIsNotNone(promo_result)
        self.assertEqual(monto, Decimal('2.00'))

    def test_asegurar_promociones_en_cotizacion_respects_tipo_cliente(self):
        from config.cotizaciones.models import Cotizacion
        from config.productos.promotions import asegurar_promociones_en_cotizacion

        cotizacion_super = Cotizacion.objects.create(cliente=self.cliente_super, estado='ENVIADA', total=0)
        item_super = CotizacionItem.objects.create(
            cotizacion=cotizacion_super, presentacion=self.presentacion, cantidad=1,
            precio=Decimal('20.00'), subtotal=Decimal('20.00'), descuento_aplicado=False, descuento_monto=Decimal('0.00'),
        )
        self.assertTrue(asegurar_promociones_en_cotizacion(cotizacion_super))
        item_super.refresh_from_db()
        self.assertTrue(item_super.descuento_aplicado)
        self.assertEqual(item_super.descuento_monto, Decimal('2.00'))

        cotizacion_dist = Cotizacion.objects.create(cliente=self.cliente_dist, estado='ENVIADA', total=0)
        item_dist = CotizacionItem.objects.create(
            cotizacion=cotizacion_dist, presentacion=self.presentacion, cantidad=1,
            precio=Decimal('20.00'), subtotal=Decimal('20.00'), descuento_aplicado=False, descuento_monto=Decimal('0.00'),
        )
        self.assertFalse(asegurar_promociones_en_cotizacion(cotizacion_dist))
        item_dist.refresh_from_db()
        self.assertFalse(item_dist.descuento_aplicado)


class PromocionPersistenceTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Promo Persist Cat')
        self.marca = Marca.objects.create(nombre='Promo Persist Brand')
        self.producto = Producto.objects.create(
            nombre='Persist Product', categoria=self.categoria, marca=self.marca, activo=True,
        )
        self.producto_sin_promo = Producto.objects.create(
            nombre='Regular Product', categoria=self.categoria, marca=self.marca, activo=True,
        )
        self.presentacion = Presentacion.objects.create(
            producto=self.producto, nombre='Case', unidades=12, tipo_contenido='unidad',
            precio_1=Decimal('10.00'), precio_2=Decimal('10.00'), precio_3=Decimal('10.00'),
            precio_4=Decimal('10.00'), precio_5=Decimal('10.00'),
        )
        Presentacion.objects.filter(id=self.presentacion.id).update(
            precio_1=Decimal('10.00'), precio_2=Decimal('10.00'), precio_3=Decimal('10.00'),
            precio_4=Decimal('10.00'), precio_5=Decimal('10.00'),
        )
        self.presentacion.refresh_from_db()
        promo = Promocion.objects.create(nombre='Persist 20%', descripcion='Buy 5 get 20%', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=5, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('20'))

        self.user = Usuario.objects.create_user(username='promo-client', password='secret123', role='cliente')
        self.cliente = Cliente.objects.create(
            usuario=self.user, nombre_empresa='Promo Client Co', telefono='5550009999',
            direccion='100 Promo St', ciudad='Atlanta', estado='GA', codigo_postal='30301', pais='USA',
            sales_tax_number='TX-PROMO-1', certificado_tax='certificados/test.pdf',
            nivel_precio=1, estado_revision=Cliente.REVIEW_STATUS_APPROVED, aprobado=True,
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

    def test_backoffice_quote_applies_missing_fixed_promo_when_price_is_zero(self):
        from config.cotizaciones.models import Cotizacion
        from config.productos.promotions import asegurar_promociones_en_cotizacion

        Promocion.objects.filter(producto=self.producto).delete()
        promo = Promocion.objects.create(
            nombre='Fixed $2 even without price', descripcion='10 CS $2 OFF',
            producto=self.producto, presentacion=self.presentacion, activa=True,
        )
        _crear_escala(promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('2.00'))

        cotizacion = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=0)
        item = CotizacionItem.objects.create(
            cotizacion=cotizacion, presentacion=self.presentacion, cantidad=10,
            precio=Decimal('0.00'), subtotal=Decimal('0.00'), descuento_aplicado=False, descuento_monto=Decimal('0.00'),
        )

        self.assertTrue(asegurar_promociones_en_cotizacion(cotizacion))
        item.refresh_from_db()
        self.assertTrue(item.descuento_aplicado)
        self.assertEqual(item.descuento_monto, Decimal('2.00'))

    def test_backoffice_quote_detail_auto_applies_promo_on_open(self):
        from config.cotizaciones.models import Cotizacion

        Promocion.objects.filter(producto=self.producto).delete()
        promo = Promocion.objects.create(nombre='BO open promo', descripcion='Buy 10 get $1.50 off', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('1.50'))

        admin = Usuario.objects.create_user(username='promo-bo', password='secret123', role='admin')
        cotizacion = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=0)
        item = CotizacionItem.objects.create(
            cotizacion=cotizacion, presentacion=self.presentacion, cantidad=10,
            precio=Decimal('0.00'), subtotal=Decimal('0.00'), descuento_aplicado=False, descuento_monto=Decimal('0.00'),
        )

        client = DjangoClient()
        client.force_login(admin)
        response = client.get(reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]))
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertTrue(item.descuento_aplicado)
        self.assertEqual(item.descuento_monto, Decimal('1.50'))
        self.assertContains(response, 'Apply discount')
        self.assertContains(response, 'checked')

    def test_percent_promo_uses_list_price_when_line_price_is_zero(self):
        Promocion.objects.filter(producto=self.producto).delete()
        Presentacion.objects.filter(id=self.presentacion.id).update(precio_1=Decimal('20.00'))
        self.presentacion.refresh_from_db()
        promo = Promocion.objects.create(nombre='Percent fallback', producto=self.producto, activa=True)
        _crear_escala(promo, cantidad_minima=5, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('20'))

        item = {
            'producto_id': self.producto.id,
            'presentacion_id': self.presentacion.id,
            'cantidad': 5,
            'precio': 0,
        }
        aplicar_promocion_en_item_sesion(item, precio_unitario=0, presentacion=self.presentacion)
        self.assertTrue(item['descuento_aplicado'])
        self.assertEqual(float(item['descuento_monto']), 4.0)

    def test_catalogo_shows_promotion_badge(self):
        client = DjangoClient()
        client.force_login(self.user)
        response = client.get(reverse('catalogo'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Promotion')
        self.assertContains(response, 'Buy 5 get 20%')
        self.assertContains(response, 'Add Promotion to Order')
        self.assertContains(response, 'View discounts')
        self.assertContains(response, 'There are products on promotion!')

    def test_catalogo_shows_all_promotion_tiers(self):
        Promocion.objects.filter(producto=self.producto).delete()
        promo = Promocion.objects.create(
            nombre='PRUEBA PROMOCION',
            descripcion='10 CS $10 OFF',
            producto=self.producto,
            activa=True,
        )
        _crear_escala(promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('0.25'))
        _crear_escala(promo, cantidad_minima=20, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('0.50'))

        client = DjangoClient()
        client.force_login(self.user)
        response = client.get(reverse('catalogo'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View discounts')
        self.assertContains(response, 'data-minimum="10"')
        self.assertContains(response, 'data-minimum="20"')
        self.assertContains(response, '10+ units')
        self.assertContains(response, '20+ units')
        self.assertContains(response, 'Get USD 0.25 off/unit.')
        self.assertContains(response, 'Get USD 0.50 off/unit.')

    def test_catalogo_shows_countdown_and_my_order_attention_with_cart(self):
        Promocion.objects.filter(producto=self.producto).update(fecha_fin=timezone.now() + timedelta(days=2, hours=3))
        client = DjangoClient()
        client.force_login(self.user)
        session = client.session
        session['carrito'] = {
            str(self.presentacion.id): {
                'producto_id': self.producto.id,
                'presentacion_id': self.presentacion.id,
                'nombre': self.producto.nombre,
                'cantidad': 10,
                'precio': 10.0,
            }
        }
        session.save()

        response = client.get(reverse('catalogo'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'js-promo-countdown')
        self.assertContains(response, 'data-ends-at=')
        self.assertContains(response, 'my-order-cta--attention')
        self.assertContains(response, 'id="contadorCarrito">10')
        self.assertEqual(response.context['carrito_total_items'], 10)

    def test_promotions_filter_and_search_only_return_active_promo_products(self):
        client = DjangoClient()
        response = client.get(reverse('catalogo'), {'promociones': '1', 'q': 'Persist'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.producto.nombre)
        self.assertNotContains(response, self.producto_sin_promo.nombre)
        self.assertEqual(response.context['filter_promociones'], '1')

    def test_cart_quantity_response_warns_below_minimum_and_applies_at_threshold(self):
        client = DjangoClient()
        client.force_login(self.user)
        session = client.session
        session['carrito'] = {
            str(self.presentacion.id): {
                'producto_id': self.producto.id,
                'presentacion_id': self.presentacion.id,
                'nombre': self.producto.nombre,
                'cantidad': 4,
                'precio': 10.0,
            }
        }
        session.save()

        below = client.post(reverse('actualizar_cantidad'), {'producto_id': str(self.presentacion.id), 'accion': 'set', 'cantidad': '4'})
        self.assertEqual(below.status_code, 200)
        self.assertTrue(below.json()['promo']['available'])
        self.assertFalse(below.json()['promo']['applied'])
        self.assertEqual(below.json()['promo']['minimum'], 5)

        threshold = client.post(reverse('actualizar_cantidad'), {'producto_id': str(self.presentacion.id), 'accion': 'set', 'cantidad': '5'})
        self.assertEqual(threshold.status_code, 200)
        self.assertTrue(threshold.json()['promo']['applied'])

    def test_add_promotion_button_ensures_minimum_instead_of_adding_it_twice(self):
        client = DjangoClient()
        client.force_login(self.user)
        session = client.session
        session['carrito'] = {
            str(self.presentacion.id): {
                'producto_id': self.producto.id,
                'presentacion_id': self.presentacion.id,
                'nombre': self.producto.nombre,
                'cantidad': 2,
                'precio': 10.0,
            }
        }
        session.save()

        response = client.post(reverse('agregar_a_cotizacion'), {
            'presentacion_id': str(self.presentacion.id),
            'cantidad': '5',
            'promo_minimum': '1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.session['carrito'][str(self.presentacion.id)]['cantidad'], 5)
        self.assertTrue(client.session['carrito'][str(self.presentacion.id)]['descuento_aplicado'])


class PromocionAdminCrudTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Promo Admin Cat')
        self.marca = Marca.objects.create(nombre='Promo Admin Brand')
        self.producto = Producto.objects.create(
            nombre='Admin Promo Product', categoria=self.categoria, marca=self.marca, activo=True,
            codigo_barras='SKU-ADMIN-001',
        )
        self.presentacion = Presentacion.objects.create(
            producto=self.producto, nombre='Case', unidades=12, tipo_contenido='unidad', precio_1=Decimal('10.00'),
        )
        Presentacion.objects.filter(id=self.presentacion.id).update(precio_1=Decimal('10.00'), costo=None)
        self.admin = Usuario.objects.create_user(username='promo-admin', password='secret123', role='admin')
        self.supermercados = TipoCliente.objects.create(codigo='super-admin-test', nombre='Supermarkets')

    def _base_escalas_payload(self, **overrides):
        payload = {
            'escalas-TOTAL_FORMS': '1',
            'escalas-INITIAL_FORMS': '0',
            'escalas-MIN_NUM_FORMS': '1',
            'escalas-MAX_NUM_FORMS': '1000',
            'escalas-0-id': '',
            'escalas-0-cantidad_minima': '5',
            'escalas-0-tipo_beneficio': PromocionEscala.TIPO_PERCENT,
            'escalas-0-valor_beneficio': '20.00',
            'escalas-0-unidades_gratis': '',
            'escalas-0-orden': '0',
        }
        payload.update(overrides)
        return payload

    def test_create_form_renders_search_widgets_instead_of_full_product_list(self):
        client = DjangoClient()
        client.force_login(self.admin)
        response = client.get(reverse('crear_promocion'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'promoProductoBuscador')
        self.assertNotContains(response, self.producto.nombre)

    def test_buscar_productos_promocion_requires_min_chars_and_matches_by_name_or_code(self):
        client = DjangoClient()
        client.force_login(self.admin)

        short = client.get(reverse('buscar_productos_promocion'), {'q': 'A'})
        self.assertEqual(short.json()['results'], [])

        by_name = client.get(reverse('buscar_productos_promocion'), {'q': 'Admin Promo'})
        self.assertEqual(len(by_name.json()['results']), 1)
        self.assertEqual(by_name.json()['results'][0]['id'], self.producto.id)

        by_code = client.get(reverse('buscar_productos_promocion'), {'q': 'SKU-ADMIN'})
        self.assertEqual(len(by_code.json()['results']), 1)

    def test_producto_presentaciones_promocion_returns_only_that_products_presentations(self):
        otro_producto = Producto.objects.create(nombre='Other product', categoria=self.categoria, marca=self.marca, activo=True)
        Presentacion.objects.create(producto=otro_producto, nombre='Other case', unidades=6, tipo_contenido='unidad')

        client = DjangoClient()
        client.force_login(self.admin)
        response = client.get(reverse('producto_presentaciones_promocion', args=[self.producto.id]))
        results = response.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.presentacion.id)

    def test_create_promotion_with_one_scale_and_customer_type(self):
        client = DjangoClient()
        client.force_login(self.admin)
        payload = {
            'nombre': 'Percent Promo',
            'descripcion': 'Buy 5 get 20%',
            'producto': str(self.producto.id),
            'presentacion': '',
            'tipos_cliente': [str(self.supermercados.id)],
            'fecha_inicio': '',
            'fecha_fin': '',
            'activa': '1',
        }
        payload.update(self._base_escalas_payload())
        response = client.post(reverse('crear_promocion'), payload)
        self.assertEqual(response.status_code, 302)
        promo = Promocion.objects.get(nombre='Percent Promo')
        self.assertEqual(promo.escalas.count(), 1)
        escala = promo.escalas.first()
        self.assertEqual(escala.tipo_beneficio, PromocionEscala.TIPO_PERCENT)
        self.assertEqual(escala.valor_beneficio, Decimal('20.00'))
        self.assertIn(self.supermercados, promo.tipos_cliente.all())

    def test_create_promotion_with_multiple_scales(self):
        client = DjangoClient()
        client.force_login(self.admin)
        payload = {
            'nombre': 'Multi scale promo',
            'descripcion': '',
            'producto': str(self.producto.id),
            'presentacion': '',
            'fecha_inicio': '',
            'fecha_fin': '',
            'activa': '1',
            'escalas-TOTAL_FORMS': '3',
            'escalas-INITIAL_FORMS': '0',
            'escalas-MIN_NUM_FORMS': '1',
            'escalas-MAX_NUM_FORMS': '1000',
            'escalas-0-id': '', 'escalas-0-cantidad_minima': '12', 'escalas-0-tipo_beneficio': PromocionEscala.TIPO_PERCENT,
            'escalas-0-valor_beneficio': '5', 'escalas-0-unidades_gratis': '', 'escalas-0-orden': '0',
            'escalas-1-id': '', 'escalas-1-cantidad_minima': '24', 'escalas-1-tipo_beneficio': PromocionEscala.TIPO_PERCENT,
            'escalas-1-valor_beneficio': '10', 'escalas-1-unidades_gratis': '', 'escalas-1-orden': '0',
            'escalas-2-id': '', 'escalas-2-cantidad_minima': '10', 'escalas-2-tipo_beneficio': PromocionEscala.TIPO_FREE_UNITS,
            'escalas-2-valor_beneficio': '', 'escalas-2-unidades_gratis': '1', 'escalas-2-orden': '0',
        }
        response = client.post(reverse('crear_promocion'), payload)
        self.assertEqual(response.status_code, 302)
        promo = Promocion.objects.get(nombre='Multi scale promo')
        self.assertEqual(promo.escalas.count(), 3)

    def test_reject_promotion_without_any_scale(self):
        client = DjangoClient()
        client.force_login(self.admin)
        payload = {
            'nombre': 'No scale promo',
            'descripcion': '',
            'producto': str(self.producto.id),
            'presentacion': '',
            'fecha_inicio': '',
            'fecha_fin': '',
            'activa': '1',
            'escalas-TOTAL_FORMS': '1',
            'escalas-INITIAL_FORMS': '0',
            'escalas-MIN_NUM_FORMS': '1',
            'escalas-MAX_NUM_FORMS': '1000',
            'escalas-0-id': '',
            'escalas-0-cantidad_minima': '',
            'escalas-0-tipo_beneficio': PromocionEscala.TIPO_PERCENT,
            'escalas-0-valor_beneficio': '',
            'escalas-0-unidades_gratis': '',
            'escalas-0-orden': '0',
            'escalas-0-DELETE': '',
        }
        response = client.post(reverse('crear_promocion'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Promocion.objects.filter(nombre='No scale promo').exists())

    def test_edit_promotion_updates_scale_and_removes_another(self):
        promo = Promocion.objects.create(nombre='Editable promo', producto=self.producto, activa=True)
        escala_a = _crear_escala(promo, cantidad_minima=5, valor_beneficio=Decimal('10'))
        escala_b = _crear_escala(promo, cantidad_minima=10, valor_beneficio=Decimal('20'))

        client = DjangoClient()
        client.force_login(self.admin)
        payload = {
            'nombre': 'Editable promo',
            'descripcion': '',
            'producto': str(self.producto.id),
            'presentacion': '',
            'fecha_inicio': '',
            'fecha_fin': '',
            'activa': '1',
            'escalas-TOTAL_FORMS': '2',
            'escalas-INITIAL_FORMS': '2',
            'escalas-MIN_NUM_FORMS': '1',
            'escalas-MAX_NUM_FORMS': '1000',
            'escalas-0-id': str(escala_a.id), 'escalas-0-cantidad_minima': '5', 'escalas-0-tipo_beneficio': PromocionEscala.TIPO_PERCENT,
            'escalas-0-valor_beneficio': '15', 'escalas-0-unidades_gratis': '', 'escalas-0-orden': '0',
            'escalas-1-id': str(escala_b.id), 'escalas-1-cantidad_minima': '10', 'escalas-1-tipo_beneficio': PromocionEscala.TIPO_PERCENT,
            'escalas-1-valor_beneficio': '20', 'escalas-1-unidades_gratis': '', 'escalas-1-orden': '0', 'escalas-1-DELETE': 'on',
        }
        response = client.post(reverse('editar_promocion', args=[promo.id]), payload)
        self.assertEqual(response.status_code, 302)
        promo.refresh_from_db()
        self.assertEqual(promo.escalas.count(), 1)
        escala_a.refresh_from_db()
        self.assertEqual(escala_a.valor_beneficio, Decimal('15.00'))
        self.assertFalse(PromocionEscala.objects.filter(id=escala_b.id).exists())

    def test_list_tabs_search_filters_and_delete(self):
        from config.productos.views import ADMIN_PROMOCIONES_PAGE_SIZE

        self.assertEqual(ADMIN_PROMOCIONES_PAGE_SIZE, 50)

        active = Promocion.objects.create(nombre='Active Promo Tab', descripcion='Active deal', producto=self.producto, activa=True)
        _crear_escala(active, cantidad_minima=5, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('10'))
        inactive = Promocion.objects.create(nombre='Inactive Promo Tab', descripcion='Old deal', producto=self.producto, activa=False)
        _crear_escala(inactive, cantidad_minima=3, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('1.00'))

        client = DjangoClient()
        client.force_login(self.admin)

        active_response = client.get(reverse('lista_promociones'), {'estado': 'activas'})
        self.assertEqual(active_response.status_code, 200)
        self.assertContains(active_response, 'Active promotions')
        self.assertContains(active_response, 'Inactive promotions')
        self.assertContains(active_response, active.nombre)
        self.assertNotContains(active_response, inactive.nombre)
        self.assertContains(active_response, 'buscadorPromociones')
        self.assertContains(active_response, 'filtroProductoPromoBuscador')
        self.assertContains(active_response, 'Delete')

        inactive_response = client.get(reverse('lista_promociones'), {'estado': 'inactivas'})
        self.assertEqual(inactive_response.status_code, 200)
        self.assertContains(inactive_response, inactive.nombre)
        self.assertNotContains(inactive_response, active.nombre)

        search_response = client.get(reverse('lista_promociones'), {
            'estado': 'activas', 'q': 'Active Promo', 'producto': str(self.producto.id), 'tipo': PromocionEscala.TIPO_PERCENT,
        })
        self.assertEqual(search_response.status_code, 200)
        self.assertContains(search_response, active.nombre)
        self.assertEqual(search_response.context['page_obj'].paginator.count, 1)

        delete_response = client.post(reverse('eliminar_promocion', args=[inactive.id]))
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(Promocion.objects.filter(id=inactive.id).exists())


class PromocionComboTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Combo Cat')
        self.marca = Marca.objects.create(nombre='Combo Brand')
        self.producto_a = Producto.objects.create(nombre='Jarrito Fresa', categoria=self.categoria, marca=self.marca, activo=True)
        self.producto_b = Producto.objects.create(nombre='Jarrito Mango', categoria=self.categoria, marca=self.marca, activo=True)
        self.producto_c = Producto.objects.create(nombre='Jarrito Limon', categoria=self.categoria, marca=self.marca, activo=True)
        self.presentacion_a = Presentacion.objects.create(producto=self.producto_a, nombre='Case A', unidades=12, tipo_contenido='unidad')
        self.presentacion_b = Presentacion.objects.create(producto=self.producto_b, nombre='Case B', unidades=12, tipo_contenido='unidad')
        self.presentacion_c = Presentacion.objects.create(producto=self.producto_c, nombre='Case C', unidades=12, tipo_contenido='unidad')
        Presentacion.objects.filter(id__in=[self.presentacion_a.id, self.presentacion_b.id, self.presentacion_c.id]).update(
            precio_1=Decimal('20.00'), precio_2=Decimal('20.00'), precio_3=Decimal('20.00'),
            precio_4=Decimal('20.00'), precio_5=Decimal('20.00'),
        )
        self.presentacion_a.refresh_from_db()
        self.presentacion_b.refresh_from_db()
        self.presentacion_c.refresh_from_db()

        self.promo = Promocion.objects.create(
            nombre='Combo Jarritos',
            descripcion='Buy 10 mixed units get 10% off',
            alcance=Promocion.ALCANCE_GRUPO,
            producto=self.producto_a,
            activa=True,
        )
        PromocionProducto.objects.create(promocion=self.promo, producto=self.producto_a)
        PromocionProducto.objects.create(promocion=self.promo, producto=self.producto_b)
        PromocionProducto.objects.create(promocion=self.promo, producto=self.producto_c)
        _crear_escala(self.promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('10'))

    def _crear_cliente(self, username='combo-client'):
        user = Usuario.objects.create_user(username=username, password='secret123', role='cliente')
        Cliente.objects.create(
            usuario=user, nombre_empresa=f'{username} Co', telefono='5551110000',
            direccion='1 St', ciudad='Atlanta', estado='GA', codigo_postal='30301', pais='USA',
            sales_tax_number=f'TX-{username}', certificado_tax='certificados/test.pdf',
            nivel_precio=1, estado_revision=Cliente.REVIEW_STATUS_APPROVED, aprobado=True,
        )
        return user

    def _carrito_combo(self):
        return {
            str(self.presentacion_a.id): {
                'producto_id': self.producto_a.id,
                'presentacion_id': self.presentacion_a.id,
                'cantidad': 5,
                'precio': 20.0,
            },
            str(self.presentacion_b.id): {
                'producto_id': self.producto_b.id,
                'presentacion_id': self.presentacion_b.id,
                'cantidad': 3,
                'precio': 20.0,
            },
            str(self.presentacion_c.id): {
                'producto_id': self.producto_c.id,
                'presentacion_id': self.presentacion_c.id,
                'cantidad': 2,
                'precio': 20.0,
            },
        }

    def test_combo_sums_quantities_before_applying_discount(self):
        carrito = self._carrito_combo()
        reaplicar_promociones_en_lineas_sesion(carrito)
        for item in carrito.values():
            self.assertTrue(item['descuento_aplicado'])
            self.assertEqual(float(item['descuento_monto']), 2.0)

        carrito[str(self.presentacion_c.id)]['cantidad'] = 1
        reaplicar_promociones_en_lineas_sesion(carrito)
        for item in carrito.values():
            self.assertFalse(item['descuento_aplicado'])

    def test_combo_percent_respects_each_line_price(self):
        Presentacion.objects.filter(id=self.presentacion_b.id).update(precio_1=Decimal('30.00'))
        carrito = {
            str(self.presentacion_a.id): {
                'producto_id': self.producto_a.id,
                'presentacion_id': self.presentacion_a.id,
                'cantidad': 5,
                'precio': 20.0,
            },
            str(self.presentacion_b.id): {
                'producto_id': self.producto_b.id,
                'presentacion_id': self.presentacion_b.id,
                'cantidad': 5,
                'precio': 30.0,
            },
        }
        reaplicar_promociones_en_lineas_sesion(carrito)
        self.assertEqual(float(carrito[str(self.presentacion_a.id)]['descuento_monto']), 2.0)
        self.assertEqual(float(carrito[str(self.presentacion_b.id)]['descuento_monto']), 3.0)

    def test_combo_fixed_discount_per_unit(self):
        Promocion.objects.all().delete()
        promo = Promocion.objects.create(
            nombre='Combo fixed',
            alcance=Promocion.ALCANCE_GRUPO,
            producto=self.producto_a,
            activa=True,
        )
        PromocionProducto.objects.create(promocion=promo, producto=self.producto_a)
        PromocionProducto.objects.create(promocion=promo, producto=self.producto_b)
        _crear_escala(promo, cantidad_minima=10, tipo_beneficio=PromocionEscala.TIPO_FIXED, valor_beneficio=Decimal('1.50'))

        carrito = {
            str(self.presentacion_a.id): {
                'producto_id': self.producto_a.id,
                'presentacion_id': self.presentacion_a.id,
                'cantidad': 6,
                'precio': 20.0,
            },
            str(self.presentacion_b.id): {
                'producto_id': self.producto_b.id,
                'presentacion_id': self.presentacion_b.id,
                'cantidad': 4,
                'precio': 25.0,
            },
        }
        reaplicar_promociones_en_lineas_sesion(carrito)
        self.assertEqual(float(carrito[str(self.presentacion_a.id)]['descuento_monto']), 1.5)
        self.assertEqual(float(carrito[str(self.presentacion_b.id)]['descuento_monto']), 1.5)

    def test_estado_promocion_reports_group_total(self):
        carrito = self._carrito_combo()
        state = estado_promocion_para_linea(
            producto_id=self.producto_a.id,
            presentacion_id=self.presentacion_a.id,
            cantidad=5,
            precio_unitario=Decimal('20.00'),
            lineas_context=list(carrito.values()),
        )
        self.assertTrue(state['grouped'])
        self.assertEqual(state['group_total'], 10)
        self.assertTrue(state['applied'])

    def test_catalog_does_not_attach_combo_to_member_products(self):
        # Combos must NOT hijack the member product cards: each product keeps its
        # own standalone card so it can be ordered normally, below the threshold.
        productos = adjuntar_promociones_a_productos([
            self.producto_a, self.producto_b, self.producto_c,
        ])
        for producto in productos:
            self.assertIsNone(producto.promocion_activa, producto.nombre)
            self.assertFalse(producto.promocion_es_grupo, producto.nombre)

    def test_combos_para_catalogo_lists_combo_with_all_members(self):
        combos = combos_para_catalogo()
        self.assertEqual(len(combos), 1)
        combo = combos[0]
        self.assertEqual(combo['nombre'], 'Combo Jarritos')
        self.assertEqual(combo['minimo'], 10)
        self.assertEqual(combo['total_miembros'], 3)
        self.assertCountEqual(
            combo['miembros'],
            ['Jarrito Fresa', 'Jarrito Mango', 'Jarrito Limon'],
        )

    def test_catalog_renders_combo_card_and_keeps_members_normal(self):
        user = Usuario.objects.create_user(username='combo-catalog', password='secret123', role='cliente')
        Cliente.objects.create(
            usuario=user, nombre_empresa='Combo Cat Co', telefono='5551112222',
            direccion='1 St', ciudad='Atlanta', estado='GA', codigo_postal='30301', pais='USA',
            sales_tax_number='TX-COMBO', certificado_tax='certificados/test.pdf',
            nivel_precio=1, estado_revision=Cliente.REVIEW_STATUS_APPROVED, aprobado=True,
        )
        client = DjangoClient()
        client.force_login(user)
        response = client.get(reverse('catalogo'))
        self.assertEqual(response.status_code, 200)
        # A dedicated combo card is rendered with its name and the build action.
        self.assertContains(response, 'combos-section')
        self.assertContains(response, 'combo-card')
        self.assertContains(response, 'Combo Jarritos')
        self.assertContains(response, 'js-combo-add-btn')
        self.assertContains(response, 'Build combo and add')
        self.assertContains(response, 'id="comboModal"')
        self.assertContains(response, 'data-combo-url-template')
        # Member products must NOT carry the combo flag on their own card.
        self.assertNotContains(response, 'data-promo-combo="1"')

    def test_combo_miembros_endpoint_returns_all_members(self):
        user = self._crear_cliente('combo-endpoint')
        client = DjangoClient()
        client.force_login(user)
        response = client.get(reverse('combo_promocion_miembros', args=[self.promo.id]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['minimum'], 10)
        self.assertEqual(len(data['miembros']), 3)
        nombres = {m['nombre'] for m in data['miembros']}
        self.assertEqual(nombres, {'Jarrito Fresa', 'Jarrito Mango', 'Jarrito Limon'})
        for miembro in data['miembros']:
            self.assertTrue(miembro['presentaciones'])
            self.assertEqual(miembro['presentaciones'][0]['precio'], 20.0)

    def test_combo_miembros_endpoint_rejects_individual_promo(self):
        individual = Promocion.objects.create(nombre='Solo', producto=self.producto_a, activa=True)
        _crear_escala(individual, cantidad_minima=5, valor_beneficio=Decimal('10'))
        user = self._crear_cliente('combo-endpoint-2')
        client = DjangoClient()
        client.force_login(user)
        response = client.get(reverse('combo_promocion_miembros', args=[individual.id]))
        self.assertEqual(response.status_code, 404)

    def test_distributed_combo_applies_discount_through_quote_flow(self):
        user = self._crear_cliente('combo-flow')
        client = DjangoClient()
        client.force_login(user)

        # Distribute 5 + 3 + 2 = 10 units across the three combo products.
        for presentacion, cantidad in [
            (self.presentacion_a, 5), (self.presentacion_b, 3), (self.presentacion_c, 2),
        ]:
            resp = client.post(reverse('agregar_a_cotizacion'), {
                'presentacion_id': str(presentacion.id),
                'cantidad': str(cantidad),
            })
            self.assertEqual(resp.status_code, 200)

        # The My Order page must show the combo discount applied on every line.
        ver = client.get(reverse('ver_cotizacion'))
        self.assertEqual(ver.status_code, 200)
        for row in ver.context['carrito']:
            self.assertTrue(row['promocion_estado']['applied'], row['producto'].nombre)
            self.assertEqual(row['promocion_estado']['group_total'], 10)

        # Saving the order persists the discount on every combo line.
        guardar = client.post(reverse('guardar_cotizacion'), {'nota': 'combo'})
        self.assertEqual(guardar.status_code, 302)
        items = CotizacionItem.objects.filter(presentacion__in=[
            self.presentacion_a, self.presentacion_b, self.presentacion_c,
        ])
        self.assertEqual(items.count(), 3)
        for item in items:
            self.assertTrue(item.descuento_aplicado)
            self.assertEqual(item.descuento_monto, Decimal('2.00'))

    def test_below_minimum_distributed_combo_does_not_apply(self):
        user = self._crear_cliente('combo-flow-low')
        client = DjangoClient()
        client.force_login(user)
        for presentacion, cantidad in [
            (self.presentacion_a, 3), (self.presentacion_b, 2), (self.presentacion_c, 2),
        ]:
            client.post(reverse('agregar_a_cotizacion'), {
                'presentacion_id': str(presentacion.id),
                'cantidad': str(cantidad),
            })
        ver = client.get(reverse('ver_cotizacion'))
        for row in ver.context['carrito']:
            self.assertFalse(row['promocion_estado']['applied'])
            self.assertEqual(row['promocion_estado']['group_total'], 7)

    def test_create_combo_promotion_via_admin(self):
        otro_a = Producto.objects.create(nombre='Combo Admin A', categoria=self.categoria, marca=self.marca, activo=True)
        otro_b = Producto.objects.create(nombre='Combo Admin B', categoria=self.categoria, marca=self.marca, activo=True)
        admin = Usuario.objects.create_user(username='combo-admin', password='secret123', role='admin')
        client = DjangoClient()
        client.force_login(admin)
        payload = {
            'nombre': 'Admin combo promo',
            'descripcion': 'Mixed 10 units',
            'alcance': Promocion.ALCANCE_GRUPO,
            'producto': '',
            'presentacion': '',
            'fecha_inicio': '',
            'fecha_fin': '',
            'activa': '1',
            'productos-TOTAL_FORMS': '2',
            'productos-INITIAL_FORMS': '0',
            'productos-MIN_NUM_FORMS': '0',
            'productos-MAX_NUM_FORMS': '1000',
            'productos-0-id': '',
            'productos-0-producto': str(otro_a.id),
            'productos-0-presentacion': '',
            'productos-1-id': '',
            'productos-1-producto': str(otro_b.id),
            'productos-1-presentacion': '',
            'escalas-TOTAL_FORMS': '1',
            'escalas-INITIAL_FORMS': '0',
            'escalas-MIN_NUM_FORMS': '1',
            'escalas-MAX_NUM_FORMS': '1000',
            'escalas-0-id': '',
            'escalas-0-cantidad_minima': '10',
            'escalas-0-tipo_beneficio': PromocionEscala.TIPO_PERCENT,
            'escalas-0-valor_beneficio': '10',
            'escalas-0-unidades_gratis': '',
            'escalas-0-orden': '0',
        }
        response = client.post(reverse('crear_promocion'), payload)
        self.assertEqual(response.status_code, 302, response.content.decode() if response.status_code != 302 else '')
        promo = Promocion.objects.get(nombre='Admin combo promo')
        self.assertEqual(promo.alcance, Promocion.ALCANCE_GRUPO)
        self.assertEqual(promo.productos_grupo.count(), 2)
