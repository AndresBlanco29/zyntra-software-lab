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
    estado_promocion_para_linea,
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

        state = estado_promocion_para_linea(
            producto_id=self.producto.id,
            presentacion_id=self.presentacion.id,
            cantidad=3,
            precio_unitario=Decimal('20.00'),
        )
        self.assertTrue(state['available'])
        self.assertFalse(state['applied'])
        self.assertEqual(state['minimum'], 10)
        self.assertEqual(state['missing'], 7)


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
        self.producto_sin_promo = Producto.objects.create(
            nombre='Regular Product',
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

    def test_backoffice_quote_applies_missing_fixed_promo_when_price_is_zero(self):
        from config.cotizaciones.models import Cotizacion
        from config.productos.promotions import asegurar_promociones_en_cotizacion

        Promocion.objects.filter(producto=self.producto).delete()
        Promocion.objects.create(
            nombre='Fixed $2 even without price',
            descripcion='10 CS $2 OFF',
            producto=self.producto,
            presentacion=self.presentacion,
            cantidad_minima=10,
            tipo_beneficio=Promocion.TIPO_FIXED,
            valor_beneficio=Decimal('2.00'),
            activa=True,
        )
        cotizacion = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=0)
        item = CotizacionItem.objects.create(
            cotizacion=cotizacion,
            presentacion=self.presentacion,
            cantidad=10,
            precio=Decimal('0.00'),
            subtotal=Decimal('0.00'),
            descuento_aplicado=False,
            descuento_monto=Decimal('0.00'),
        )

        self.assertTrue(asegurar_promociones_en_cotizacion(cotizacion))
        item.refresh_from_db()
        self.assertTrue(item.descuento_aplicado)
        self.assertEqual(item.descuento_monto, Decimal('2.00'))

    def test_backoffice_quote_detail_auto_applies_promo_on_open(self):
        from config.cotizaciones.models import Cotizacion

        Promocion.objects.filter(producto=self.producto).delete()
        Promocion.objects.create(
            nombre='BO open promo',
            descripcion='Buy 10 get $1.50 off',
            producto=self.producto,
            cantidad_minima=10,
            tipo_beneficio=Promocion.TIPO_FIXED,
            valor_beneficio=Decimal('1.50'),
            activa=True,
        )
        admin = Usuario.objects.create_user(username='promo-bo', password='secret123', role='admin')
        cotizacion = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=0)
        item = CotizacionItem.objects.create(
            cotizacion=cotizacion,
            presentacion=self.presentacion,
            cantidad=10,
            precio=Decimal('0.00'),
            subtotal=Decimal('0.00'),
            descuento_aplicado=False,
            descuento_monto=Decimal('0.00'),
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
        from config.productos.promotions import aplicar_promocion_en_item_sesion

        Promocion.objects.filter(producto=self.producto).delete()
        Presentacion.objects.filter(id=self.presentacion.id).update(precio_1=Decimal('20.00'))
        self.presentacion.refresh_from_db()
        Promocion.objects.create(
            nombre='Percent fallback',
            producto=self.producto,
            cantidad_minima=5,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('20'),
            activa=True,
        )
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
        self.assertContains(response, 'Minimum: 5 units')
        self.assertContains(response, 'There are products on promotion!')

    def test_catalogo_shows_countdown_and_my_order_attention_with_cart(self):
        Promocion.objects.filter(producto=self.producto).update(
            fecha_fin=timezone.now() + timedelta(days=2, hours=3),
        )
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
        response = client.get(reverse('catalogo'), {
            'promociones': '1',
            'q': 'Persist',
        })
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

        below = client.post(reverse('actualizar_cantidad'), {
            'producto_id': str(self.presentacion.id),
            'accion': 'set',
            'cantidad': '4',
        })
        self.assertEqual(below.status_code, 200)
        self.assertTrue(below.json()['promo']['available'])
        self.assertFalse(below.json()['promo']['applied'])
        self.assertEqual(below.json()['promo']['minimum'], 5)

        threshold = client.post(reverse('actualizar_cantidad'), {
            'producto_id': str(self.presentacion.id),
            'accion': 'set',
            'cantidad': '5',
        })
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


class PromocionAdminBenefitValueTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Promo Admin Cat')
        self.marca = Marca.objects.create(nombre='Promo Admin Brand')
        self.producto = Producto.objects.create(
            nombre='Admin Promo Product',
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
        )
        Presentacion.objects.filter(id=self.presentacion.id).update(
            precio_1=Decimal('10.00'),
            costo=None,
        )
        self.admin = Usuario.objects.create_user(
            username='promo-admin',
            password='secret123',
            role='admin',
        )

    def test_create_form_shows_percentage_dropdown_and_fixed_presets(self):
        client = DjangoClient()
        client.force_login(self.admin)
        response = client.get(reverse('crear_promocion'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'promoValorPorcentaje')
        self.assertContains(response, 'Discount 1')
        self.assertContains(response, 'promoValorFijoPreset')
        self.assertTrue(len(response.context['percentage_preset_options']) >= 5)
        self.assertTrue(len(response.context['fixed_preset_options']) >= 1)

    def test_create_percentage_promotion_from_preset_dropdown(self):
        client = DjangoClient()
        client.force_login(self.admin)
        response = client.post(reverse('crear_promocion'), {
            'nombre': 'Percent Promo',
            'descripcion': 'Buy 5 get 20%',
            'producto': str(self.producto.id),
            'presentacion': '',
            'cantidad_minima': '5',
            'tipo_beneficio': Promocion.TIPO_PERCENT,
            'valor_beneficio': '20.00',
            'activa': '1',
        })
        self.assertEqual(response.status_code, 302)
        promo = Promocion.objects.get(nombre='Percent Promo')
        self.assertEqual(promo.tipo_beneficio, Promocion.TIPO_PERCENT)
        self.assertEqual(promo.valor_beneficio, Decimal('20.00'))

    def test_reject_non_preset_percentage_on_create(self):
        client = DjangoClient()
        client.force_login(self.admin)
        response = client.post(reverse('crear_promocion'), {
            'nombre': 'Bad Percent Promo',
            'descripcion': 'Invalid %',
            'producto': str(self.producto.id),
            'presentacion': '',
            'cantidad_minima': '5',
            'tipo_beneficio': Promocion.TIPO_PERCENT,
            'valor_beneficio': '17.50',
            'activa': '1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Promocion.objects.filter(nombre='Bad Percent Promo').exists())
        self.assertContains(response, 'configured percentage')

    def test_create_fixed_promotion_from_orders_preset_amount(self):
        from config.productos.models import ConfiguracionDescuentos

        preset_amount = ConfiguracionDescuentos.obtener().descuento_6
        client = DjangoClient()
        client.force_login(self.admin)
        response = client.post(reverse('crear_promocion'), {
            'nombre': 'Fixed Promo',
            'descripcion': 'Buy 3 save fixed',
            'producto': str(self.producto.id),
            'presentacion': str(self.presentacion.id),
            'cantidad_minima': '3',
            'tipo_beneficio': Promocion.TIPO_FIXED,
            'valor_beneficio': format(preset_amount, '.2f'),
            'activa': '1',
        })
        self.assertEqual(response.status_code, 302)
        promo = Promocion.objects.get(nombre='Fixed Promo')
        self.assertEqual(promo.tipo_beneficio, Promocion.TIPO_FIXED)
        self.assertEqual(promo.valor_beneficio, preset_amount)

    def test_quote_discount_matches_orders_preset_after_promo_save(self):
        from config.cotizaciones.models import Cotizacion
        from config.cotizaciones.views import _build_quote_item_rows, _match_discount_preset_key, _build_quote_discount_preset_options
        from config.pedidos.services import crear_pedido_desde_items
        from config.productos.models import ConfiguracionDescuentos

        preset_amount = ConfiguracionDescuentos.obtener().descuento_6
        Promocion.objects.create(
            nombre='Fixed match',
            producto=self.producto,
            presentacion=self.presentacion,
            cantidad_minima=1,
            tipo_beneficio=Promocion.TIPO_FIXED,
            valor_beneficio=preset_amount,
            activa=True,
        )
        cliente_user = Usuario.objects.create_user(username='promo-order-client', password='secret123', role='cliente')
        cliente = Cliente.objects.create(
            usuario=cliente_user,
            nombre_empresa='Order Promo Co',
            telefono='5550008888',
            direccion='100 Order St',
            ciudad='Atlanta',
            estado='GA',
            codigo_postal='30301',
            pais='USA',
            sales_tax_number='TX-PROMO-2',
            certificado_tax='certificados/test.pdf',
            nivel_precio=1,
            estado_revision=Cliente.REVIEW_STATUS_APPROVED,
            aprobado=True,
        )
        cotizacion = Cotizacion.objects.create(cliente=cliente, estado='ENVIADA', total=0)
        item = CotizacionItem.objects.create(
            cotizacion=cotizacion,
            presentacion=self.presentacion,
            cantidad=3,
            precio=Decimal('10.00'),
            subtotal=Decimal('24.00'),
            descuento_aplicado=True,
            descuento_monto=preset_amount,
        )
        rows, _ = _build_quote_item_rows(cotizacion)
        self.assertEqual(rows[0]['selected_discount_preset_key'], 'descuento_6')
        self.assertEqual(
            _match_discount_preset_key(_build_quote_discount_preset_options(), item.descuento_monto),
            'descuento_6',
        )

        pedido = crear_pedido_desde_items(
            cliente=cliente,
            items_payload=[{
                'presentacion': self.presentacion,
                'cantidad': 3,
                'precio': Decimal('10.00'),
                'descuento_aplicado': True,
                'descuento_monto': preset_amount,
            }],
            origen='BACKOFFICE',
            vendedor=None,
            bypass_stock_check=True,
            reservar_inventario=False,
        )
        order_item = pedido.items.get()
        self.assertTrue(order_item.descuento_aplicado)
        self.assertEqual(order_item.descuento_monto, preset_amount)
        self.assertEqual(
            _match_discount_preset_key(_build_quote_discount_preset_options(), order_item.descuento_monto),
            'descuento_6',
        )

    def test_list_tabs_search_filters_and_delete(self):
        from config.productos.views import ADMIN_PROMOCIONES_PAGE_SIZE

        self.assertEqual(ADMIN_PROMOCIONES_PAGE_SIZE, 50)

        active = Promocion.objects.create(
            nombre='Active Promo Tab',
            descripcion='Active deal',
            producto=self.producto,
            cantidad_minima=5,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal('10'),
            activa=True,
        )
        inactive = Promocion.objects.create(
            nombre='Inactive Promo Tab',
            descripcion='Old deal',
            producto=self.producto,
            cantidad_minima=3,
            tipo_beneficio=Promocion.TIPO_FIXED,
            valor_beneficio=Decimal('1.00'),
            activa=False,
        )

        client = DjangoClient()
        client.force_login(self.admin)

        active_response = client.get(reverse('lista_promociones'), {'estado': 'activas'})
        self.assertEqual(active_response.status_code, 200)
        self.assertContains(active_response, 'Active promotions')
        self.assertContains(active_response, 'Inactive promotions')
        self.assertContains(active_response, active.nombre)
        self.assertNotContains(active_response, inactive.nombre)
        self.assertContains(active_response, 'buscadorPromociones')
        self.assertContains(active_response, 'filtroProductoPromo')
        self.assertContains(active_response, 'Delete')

        inactive_response = client.get(reverse('lista_promociones'), {'estado': 'inactivas'})
        self.assertEqual(inactive_response.status_code, 200)
        self.assertContains(inactive_response, inactive.nombre)
        self.assertNotContains(inactive_response, active.nombre)

        search_response = client.get(reverse('lista_promociones'), {
            'estado': 'activas',
            'q': 'Active Promo',
            'producto': str(self.producto.id),
            'tipo': Promocion.TIPO_PERCENT,
        })
        self.assertEqual(search_response.status_code, 200)
        self.assertContains(search_response, active.nombre)
        self.assertEqual(search_response.context['page_obj'].paginator.count, 1)

        delete_response = client.post(reverse('eliminar_promocion', args=[inactive.id]))
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(Promocion.objects.filter(id=inactive.id).exists())
