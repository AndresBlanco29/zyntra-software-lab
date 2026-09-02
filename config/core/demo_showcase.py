"""Fictitious showcase dataset for DEMO_MODE only.

Never import production dumps. All names, phones and documents are fake.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from config.clientes.models import Cliente, TipoCliente
from config.core.demo_branding import apply_demo_home_contenido
from config.core.models import HomeContenido, Testimonio
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.facturacion.models import Delivery, Invoice, InvoiceItem
from config.integrations.models import QuickBooksConnection, QuickBooksSyncRun
from config.inventario.models import StockPresentacion
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import (
    Categoria,
    Marca,
    Presentacion,
    Producto,
    Promocion,
    PromocionEscala,
)
from config.usuarios.models import Usuario

DEMO_EMAIL_DOMAIN = 'demo-system.com'
DEMO_PASSWORD = 'DemoShowcase2026!'
DEMO_TAX_CERT = SimpleUploadedFile(
    'demo-tax-certificate.txt',
    b'DEMO SOFTWARE LAB - fictitious tax certificate. Not a real document.',
    content_type='text/plain',
)

CUSTOMERS = (
    ('Harborline Market Group', 'Atlanta', 'GA', '30303', 2, 'NET14'),
    ('Plaza Fresh Wholesale', 'Miami', 'FL', '33101', 3, 'COD'),
    ('Nova Pantry Supply', 'Orlando', 'FL', '32801', 2, 'NET7'),
    ('Ridgeway Bodega Network', 'Charlotte', 'NC', '28202', 1, 'ACH_NET7'),
    ('Eastgate Cash & Carry', 'Tampa', 'FL', '33602', 4, 'NET21'),
)

CATALOG = (
    ('Beverages', 'AquaPura', (
        ('Sparkling Water 12oz', 'Case 24', 24, '8.50'),
        ('Still Water 1L', 'Case 12', 12, '6.25'),
    )),
    ('Snacks', 'Crispa', (
        ('Corn Chips Classic', 'Case 20', 20, '14.00'),
        ('Plantain Chips', 'Case 16', 16, '15.50'),
        ('Mixed Nuts Tray', 'Case 12', 12, '22.00'),
    )),
    ('Dry Goods', 'CampoNorte', (
        ('White Rice 20lb', 'Bag', 1, '18.00'),
        ('Black Beans 4lb', 'Case 6', 6, '16.50'),
        ('Corn Flour 2lb', 'Case 10', 10, '12.75'),
        ('Cooking Oil 1gal', 'Case 4', 4, '28.00'),
    )),
    ('Dairy Alternatives', 'LactoFree Co', (
        ('Oat Beverage 32oz', 'Case 12', 12, '19.00'),
        ('Coconut Cream 14oz', 'Case 24', 24, '26.00'),
    )),
    ('Frozen', 'FrostPeak', (
        ('Frozen Empanadas', 'Case 24', 24, '32.00'),
    )),
    ('Bakery', 'Hornito', (
        ('Corn Tortillas 80ct', 'Case 12', 12, '21.00'),
    )),
    ('Sauces', 'SalsaBrava', (
        ('Hot Sauce Trio', 'Case 12', 12, '17.50'),
    )),
    ('Produce Pack', 'VerdeLago', (
        ('Avocado Tray', 'Case 1', 1, '24.00'),
    )),
)

# Extra brand tiles for the home carousel (name-only cards — no Tortilla logos).
SHOWCASE_BRANDS = (
    'AquaPura',
    'Crispa',
    'CampoNorte',
    'LactoFree Co',
    'FrostPeak',
    'Hornito',
    'SalsaBrava',
    'VerdeLago',
    'SolAndino',
    'MarAzul Foods',
    'TierraViva',
    'NortePack',
)

TESTIMONIALS = (
    (
        'Maya R.',
        'Harborline Market Group',
        'Harborline Market Group',
        'Pedimos más rápido y el equipo ve inventario y precios en un solo lugar. Ideal para mostrar el flujo B2B.',
        'We order faster and the team sees inventory and pricing in one place. Perfect for showing the B2B flow.',
        5,
        1,
    ),
    (
        'Luis O.',
        'Plaza Fresh Wholesale',
        'Plaza Fresh Wholesale',
        'Las cotizaciones y el seguimiento de pedidos se sienten de producto real, no de un mockup vacío.',
        'Quotes and order tracking feel like a real product, not an empty mockup.',
        5,
        2,
    ),
    (
        'Priya S.',
        'Nova Pantry Supply',
        'Nova Pantry Supply',
        'Las ofertas destacadas y las marcas dan cara al catálogo. Así se entiende qué vería un cliente.',
        'Featured offers and brands give the catalog a face. You immediately see what a customer would see.',
        5,
        3,
    ),
    (
        'Diego M.',
        'Eastgate Cash & Carry',
        'Eastgate Cash & Carry',
        'El portal se siente listo para onboarding: catálogo, promo y checkout de cotización sin drama.',
        'The portal feels onboarding-ready: catalog, promos, and quote checkout without friction.',
        4,
        4,
    ),
)


def _require_demo_mode():
    from django.conf import settings

    if not getattr(settings, 'DEMO_MODE', False):
        raise RuntimeError(
            'seed_demo_showcase refused to run: DEMO_MODE is off. '
            'This protects La Tortilla Grocery production data.'
        )


def clear_showcase_business_data():
    """Remove operational rows so the showcase can be rebuilt (DEMO only)."""
    _require_demo_mode()
    Delivery.objects.all().delete()
    InvoiceItem.objects.all().delete()
    Invoice.objects.all().delete()
    PedidoItem.objects.all().delete()
    Pedido.objects.all().delete()
    CotizacionItem.objects.all().delete()
    Cotizacion.objects.all().delete()
    Promocion.objects.all().delete()
    Testimonio.objects.all().delete()
    StockPresentacion.objects.all().delete()
    Presentacion.objects.all().delete()
    Producto.objects.all().delete()
    Marca.objects.all().delete()
    Categoria.objects.all().delete()
    Cliente.objects.all().delete()
    QuickBooksSyncRun.objects.all().delete()
    # Keep connection row shape for mock UI; clear tokens so nothing real remains.
    QuickBooksConnection.objects.all().delete()
    Usuario.objects.filter(email__iendswith=f'@{DEMO_EMAIL_DOMAIN}').delete()
    Usuario.objects.filter(username__startswith='demo_').delete()


def _user(*, username, email, role, first_name, is_staff=False):
    user, created = Usuario.objects.get_or_create(
        username=username,
        defaults={
            'email': email,
            'role': role,
            'first_name': first_name,
            'is_staff': is_staff,
            'is_active': True,
        },
    )
    if created or not user.has_usable_password():
        user.set_password(DEMO_PASSWORD)
    user.email = email
    user.role = role
    user.first_name = first_name
    user.is_staff = is_staff
    user.is_active = True
    # Demo explorer: backoffice without user-admin privileges.
    if role == 'backoffice':
        user.permission_overrides = {
            'admin.dashboard.view': True,
            'admin.users.view': False,
            'admin.users.manage': False,
            'admin.customer_requests.manage': False,
            'admin.content.manage': False,
        }
    user.save()
    return user


def _seed_users():
    return {
        'demo': _user(
            username='demo_backoffice',
            email=f'demo@{DEMO_EMAIL_DOMAIN}',
            role='backoffice',
            first_name='Demo',
            is_staff=True,
        ),
        'vendor': _user(
            username='demo_vendor',
            email=f'vendor@{DEMO_EMAIL_DOMAIN}',
            role='vendedor',
            first_name='Alex',
        ),
        'selector': _user(
            username='demo_selector',
            email=f'selector@{DEMO_EMAIL_DOMAIN}',
            role='seleccionador',
            first_name='Sam',
        ),
        'driver1': _user(
            username='demo_driver1',
            email=f'driver1@{DEMO_EMAIL_DOMAIN}',
            role='driver',
            first_name='Jordan',
        ),
        'driver2': _user(
            username='demo_driver2',
            email=f'driver2@{DEMO_EMAIL_DOMAIN}',
            role='driver',
            first_name='Riley',
        ),
    }


def _seed_tipos():
    supermarket, _ = TipoCliente.objects.get_or_create(
        codigo='supermercados',
        defaults={'nombre': 'Supermercados', 'nombre_en': 'Supermarkets', 'orden': 1},
    )
    distributor, _ = TipoCliente.objects.get_or_create(
        codigo='distribuidores',
        defaults={'nombre': 'Distribuidores', 'nombre_en': 'Distributors', 'orden': 2},
    )
    return supermarket, distributor


def _seed_customers(users, tipos):
    supermarket, distributor = tipos
    customers = []
    for index, (company, city, state, zip_code, tier, terms) in enumerate(CUSTOMERS, start=1):
        login = f'customer{index}@{DEMO_EMAIL_DOMAIN}'
        user = _user(
            username=f'demo_customer_{index}',
            email=login,
            role='cliente',
            first_name=company.split()[0],
        )
        cliente, _ = Cliente.objects.update_or_create(
            usuario=user,
            defaults={
                'nombre_empresa': company,
                'telefono': f'+1404555{1000 + index:04d}',
                'direccion': f'{100 + index * 10} Demo Commerce Blvd',
                'ciudad': city,
                'estado': state,
                'codigo_postal': zip_code,
                'pais': 'USA',
                'sales_tax_number': f'DEMO-ST-{index:04d}',
                'certificado_tax': DEMO_TAX_CERT,
                'declaracion_fiscal_aceptada': True,
                'declaracion_fiscal_aceptada_en': timezone.now(),
                'aprobado': True,
                'estado_revision': Cliente.REVIEW_STATUS_APPROVED,
                'nivel_precio': tier,
                'terminos_pago': terms,
                'credit_limit': Decimal('15000.00'),
                'balance': Decimal('0.00'),
                'tipo_cliente': supermarket if index % 2 else distributor,
                'vendedor_asignado': users['vendor'],
                'aprobado_en': timezone.now(),
            },
        )
        customers.append(cliente)
    return customers


def _seed_catalog():
    presentations = []
    for cat_name, brand_name, items in CATALOG:
        categoria, _ = Categoria.objects.get_or_create(
            nombre=cat_name,
            defaults={'nombre_en': cat_name},
        )
        marca, _ = Marca.objects.get_or_create(
            nombre=brand_name,
            defaults={'activo': True, 'nombre_en': brand_name},
        )
        if not marca.activo:
            marca.activo = True
            marca.save(update_fields=['activo'])
        marca.categorias.add(categoria)
        for product_name, pack_name, units, cost in items:
            producto, _ = Producto.objects.update_or_create(
                nombre=product_name,
                defaults={
                    'categoria': categoria,
                    'marca': marca,
                    'activo': True,
                    'descripcion': f'Demo catalog item — {product_name}',
                },
            )
            presentacion, _ = Presentacion.objects.update_or_create(
                producto=producto,
                nombre=pack_name,
                defaults={
                    'unidades': units,
                    'tipo_contenido': 'unidades',
                    'costo': Decimal(cost),
                },
            )
            StockPresentacion.objects.update_or_create(
                presentacion=presentacion,
                defaults={'stock_fisico': 120 + len(presentations) * 3},
            )
            presentations.append(presentacion)
    _seed_showcase_brands()
    return presentations


def _seed_showcase_brands():
    """Ensure home “Brands we distribute” has a full fictitious carousel."""
    for brand_name in SHOWCASE_BRANDS:
        marca, _ = Marca.objects.get_or_create(
            nombre=brand_name,
            defaults={'activo': True, 'nombre_en': brand_name},
        )
        if not marca.activo:
            marca.activo = True
            marca.save(update_fields=['activo'])


def _seed_home_promotions(presentations=None):
    """Featured offers on home — fictitious promos visitors can recognize instantly."""
    if Promocion.objects.filter(nombre__startswith='Demo ·').exists():
        return list(Promocion.objects.filter(nombre__startswith='Demo ·'))

    if presentations is None:
        presentations = list(
            Presentacion.objects.select_related('producto')
            .filter(producto__activo=True)
            .order_by('id')[:6]
        )
    now = timezone.now()
    created = []
    offer_specs = (
        ('Demo · Case savings', 'Buy 6+ cases and unlock 10% off — sample featured offer.', 6, '10.00'),
        ('Demo · Volume boost', 'Mix-ready promo tile for the home carousel.', 12, '12.00'),
        ('Demo · Starter pack', 'Example promotion customers would tap from Featured Offers.', 4, '8.00'),
        ('Demo · Weekend special', 'Placeholder deal so the section never looks empty in Software Lab.', 8, '15.00'),
    )
    for index, (name, description, min_qty, percent) in enumerate(offer_specs):
        if index >= len(presentations):
            break
        presentacion = presentations[index]
        producto = presentacion.producto
        promo = Promocion.objects.create(
            nombre=name,
            descripcion=description,
            alcance=Promocion.ALCANCE_INDIVIDUAL,
            producto=producto,
            presentacion=presentacion,
            fecha_inicio=now - timedelta(days=1),
            fecha_fin=now + timedelta(days=60),
            activa=True,
        )
        PromocionEscala.objects.create(
            promocion=promo,
            cantidad_minima=min_qty,
            tipo_beneficio=Promocion.TIPO_PERCENT,
            valor_beneficio=Decimal(percent),
            orden=0,
        )
        producto.destacado = True
        producto.save(update_fields=['destacado'])
        created.append(promo)
    return created


def _seed_home_testimonials():
    """Customer quotes for the home testimonials slider."""
    if Testimonio.objects.filter(activo=True).exists():
        return list(Testimonio.objects.filter(activo=True))

    created = []
    for nombre, negocio, negocio_en, comentario, comentario_en, estrellas, orden in TESTIMONIALS:
        created.append(
            Testimonio.objects.create(
                nombre=nombre,
                negocio=negocio,
                negocio_en=negocio_en,
                comentario=comentario,
                comentario_en=comentario_en,
                estrellas=estrellas,
                orden=orden,
                activo=True,
            )
        )
    return created


def _clear_home_marketing_caches():
    from django.core.cache import cache

    for key in (
        'home:contenido',
        'home:contenido:demo',
        'home:marcas_activas',
        'home:promociones_activas',
        'home:testimonios_activos',
        'home:productos_destacados',
    ):
        cache.delete(key)


def ensure_demo_home_marketing():
    """Idempotent fill for home brands / offers / testimonials (DEMO only)."""
    _require_demo_mode()
    _seed_showcase_brands()
    presentations = list(
        Presentacion.objects.select_related('producto')
        .filter(producto__activo=True)
        .order_by('id')[:8]
    )
    if presentations:
        _seed_home_promotions(presentations)
    _seed_home_testimonials()
    _clear_home_marketing_caches()


def _add_lines(pedido, presentations, qty_base=2):
    total = Decimal('0.00')
    items = []
    for offset, presentacion in enumerate(presentations[:3]):
        qty = qty_base + offset
        price = presentacion.precio_2 or presentacion.precio_1 or presentacion.costo
        subtotal = (price * qty).quantize(Decimal('0.01'))
        item = PedidoItem.objects.create(
            pedido=pedido,
            presentacion=presentacion,
            cantidad_solicitada=qty,
            cantidad=qty,
            precio=price,
            subtotal=subtotal,
        )
        items.append(item)
        total += subtotal
    pedido.total = total
    pedido.save(update_fields=['total'])
    return items


def _seed_quotes(customers, presentations):
    quotes = []
    for index, cliente in enumerate(customers[:3]):
        estado = ('BORRADOR', 'ENVIADA', 'LISTA_PARA_CONFIRMACION')[index]
        quote = Cotizacion.objects.create(
            cliente=cliente,
            vendedor=None,  # Usuario.role is lowercase; FK limit_choices_to uses VENDEDOR
            estado=estado,
            total=Decimal('0.00'),
            nota_cliente='Demo quote for Software Lab walkthrough.',
        )
        total = Decimal('0.00')
        for presentacion in presentations[index:index + 2]:
            qty = 4
            price = presentacion.precio_1 or presentacion.costo
            subtotal = (price * qty).quantize(Decimal('0.01'))
            CotizacionItem.objects.create(
                cotizacion=quote,
                presentacion=presentacion,
                cantidad=qty,
                precio=price,
                subtotal=subtotal,
            )
            total += subtotal
        quote.total = total
        quote.save(update_fields=['total'])
        quotes.append(quote)
    return quotes


def _seed_orders(customers, users, presentations):
    now = timezone.now()
    pipeline = [
        ('RECIBIDO', None, None, False, None),
        ('EN_GESTION', 'vendor', None, False, None),
        ('LISTO_PARA_PICKING', 'vendor', 'selector', False, None),
        ('PARA_VERIFICAR', 'vendor', 'selector', False, None),
        ('VERIFICADO_AJUSTADO', 'vendor', 'selector', False, None),
        ('INVOICE_GENERADA', 'vendor', 'selector', True, 'RUTA_DRIVER'),
        ('INVOICE_GENERADA', 'vendor', 'selector', True, 'CUSTOMER_PICK_UP'),
        ('DESPACHADO', 'vendor', 'selector', True, 'RUTA_DRIVER'),
        ('DESPACHADO', 'vendor', 'selector', True, 'LTG'),
        ('CANCELADO', 'vendor', None, False, None),
        ('RECIBIDO', 'vendor', None, False, None),
        ('EN_GESTION', 'vendor', None, False, None),
    ]
    orders = []
    for index, (estado, vendor_key, selector_key, with_invoice, delivery_method) in enumerate(pipeline):
        cliente = customers[index % len(customers)]
        pedido = Pedido.objects.create(
            cliente=cliente,
            vendedor=users[vendor_key] if vendor_key else None,
            seleccionador=users[selector_key] if selector_key else None,
            origen='VENDEDOR' if vendor_key else 'CLIENTE',
            estado=estado,
            acepta_terminos=True,
            acepta_terminos_en=now,
            nota_cliente='Showcase order — fictitious Software Lab data.',
            picking_verificado_en=now - timedelta(hours=2) if estado in {
                'VERIFICADO_AJUSTADO', 'INVOICE_GENERADA', 'DESPACHADO',
            } else None,
        )
        # Rotate catalog slices so lines look varied.
        slice_start = index % max(1, len(presentations) - 3)
        items = _add_lines(pedido, presentations[slice_start:], qty_base=2 + (index % 3))
        if with_invoice:
            _seed_invoice_for_order(
                pedido=pedido,
                items=items,
                users=users,
                delivery_method=delivery_method,
                index=index,
                mark_dispatched=(estado == 'DESPACHADO'),
            )
        orders.append(pedido)
    return orders


def _seed_invoice_for_order(*, pedido, items, users, delivery_method, index, mark_dispatched):
    driver = users['driver1'] if delivery_method == 'RUTA_DRIVER' else None
    if delivery_method == 'RUTA_DRIVER' and index % 2:
        driver = users['driver2']

    # Model.save() does not call full_clean(); safe for DESPACHADO showcase rows.
    invoice = Invoice.objects.create(
        pedido=pedido,
        cliente=pedido.cliente,
        metodo_entrega=delivery_method,
        driver=driver,
        estado='GENERADA',
        subtotal=pedido.total,
        total_neto=pedido.total,
        saldo_cliente=pedido.total if index % 3 else Decimal('0.00'),
        fecha_documento=timezone.localdate() - timedelta(days=index % 5),
        creada_por=users['demo'],
        sync_status='SYNCED' if index % 2 else 'PENDING',
        quickbooks_id=f'DEMO-QB-INV-{pedido.pk}' if index % 2 else None,
        qb_payment_status=('PAID', 'OPEN', 'OVERDUE', 'DUE')[index % 4] if index % 2 else '',
        qb_due_date=timezone.localdate() + timedelta(days=7 - index) if index % 2 else None,
        last_synced_at=timezone.now() - timedelta(hours=index) if index % 2 else None,
    )
    Invoice.objects.filter(pk=invoice.pk).update(numero=f'INV-DEMO-{invoice.pk:05d}')
    invoice.numero = f'INV-DEMO-{invoice.pk:05d}'

    for item in items:
        InvoiceItem.objects.create(
            invoice=invoice,
            pedido_item=item,
            presentacion=item.presentacion,
            producto_nombre=item.presentacion.producto.nombre,
            presentacion_nombre=item.presentacion.nombre,
            cantidad_facturada=item.cantidad,
            precio_unitario=item.precio,
            subtotal=item.subtotal,
        )

    delivery_estado = 'ASIGNADA'
    payment_estado = 'PENDIENTE'
    if mark_dispatched:
        if index % 2 == 0:
            delivery_estado = 'ENTREGADA_PAGADA'
            payment_estado = 'PAGADO'
            Invoice.objects.filter(pk=invoice.pk).update(
                saldo_cliente=Decimal('0.00'),
                qb_payment_status='PAID',
            )
        else:
            delivery_estado = 'EN_RUTA'

    is_pickup = delivery_method == 'CUSTOMER_PICK_UP'
    Delivery.objects.create(
        invoice=invoice,
        driver=driver,
        estado=delivery_estado,
        estado_pago=payment_estado,
        metodo_pago='CASH' if payment_estado == 'PAGADO' else '',
        delivery_address=pedido.cliente.direccion,
        delivery_city=pedido.cliente.ciudad,
        delivery_state=pedido.cliente.estado,
        delivery_postal_code=pedido.cliente.codigo_postal or '',
        is_customer_pickup=is_pickup,
        sent_to_driver_at=timezone.now() - timedelta(hours=5),
        route_started_at=timezone.now() - timedelta(hours=2) if delivery_estado != 'ASIGNADA' else None,
        delivered_at=timezone.now() - timedelta(hours=1) if delivery_estado.startswith('ENTREGADA') else None,
    )
    if pedido.estado != 'INVOICE_GENERADA' and not mark_dispatched:
        pass
    return invoice


def _seed_quickbooks_mock():
    from django.conf import settings

    environment = getattr(settings, 'QUICKBOOKS_ENVIRONMENT', 'sandbox') or 'sandbox'
    # Fake tokens only — never production credentials. Enough for is_active UI.
    QuickBooksConnection.objects.update_or_create(
        environment=environment,
        defaults={
            'realm_id': 'demo-mock-realm-0001',
            'access_token': 'demo-mock-access-token',
            'refresh_token': 'demo-mock-refresh-token',
            'token_type': 'Bearer',
            'scope': 'com.intuit.quickbooks.accounting',
            'access_token_expires_at': timezone.now() + timedelta(hours=1),
            'refresh_token_expires_at': timezone.now() + timedelta(days=30),
            'connected_at': timezone.now() - timedelta(days=3),
            'last_refreshed_at': timezone.now() - timedelta(hours=2),
            'last_error': '',
            'sync_state': {
                'demo_mock': True,
                'provider': 'mock',
                'label': 'Software Lab mock connection',
            },
        },
    )
    QuickBooksSyncRun.objects.all().delete()
    runs = [
        (QuickBooksSyncRun.TRIGGER_SCHEDULED, QuickBooksSyncRun.STATUS_SUCCESS, 12.4, {
            'import': {
                'customers': {'created': 0, 'updated': 5},
                'items': {'created': 0, 'updated': 12},
                'invoices': {'created': 0, 'updated': 0},
            },
            'export': {
                'customers': {'success': 0, 'failed': 0},
                'presentations': {'success': 0, 'failed': 0},
                'invoices': {'success': 4, 'failed': 0},
            },
            'demo_mock': True,
        }),
        (QuickBooksSyncRun.TRIGGER_MANUAL, QuickBooksSyncRun.STATUS_SUCCESS, 4.2, {
            'export': {'invoices': {'success': 3, 'failed': 0}},
            'demo_mock': True,
        }),
        (QuickBooksSyncRun.TRIGGER_MANUAL_FULL, QuickBooksSyncRun.STATUS_PARTIAL, 6.1, {
            'import': {
                'customers': {'created': 0, 'updated': 5},
                'items': {'created': 0, 'updated': 12},
            },
            'warnings': ['2 items skipped (demo)'],
            'demo_mock': True,
        }),
    ]
    for offset, (trigger, status, seconds, summary) in enumerate(runs):
        started = timezone.now() - timedelta(hours=offset + 1)
        run = QuickBooksSyncRun.objects.create(
            trigger=trigger,
            status=status,
            force_full=(trigger == QuickBooksSyncRun.TRIGGER_MANUAL_FULL),
            summary=summary,
            finished_at=started + timedelta(seconds=seconds),
        )
        QuickBooksSyncRun.objects.filter(pk=run.pk).update(
            started_at=started,
            finished_at=started + timedelta(seconds=seconds),
        )


@transaction.atomic
def seed_demo_showcase(*, reset=False):
    """Build a coherent fictitious operating company for video + Software Lab."""
    _require_demo_mode()
    if reset:
        clear_showcase_business_data()
    elif Cliente.objects.filter(nombre_empresa__in=[row[0] for row in CUSTOMERS]).exists():
        raise RuntimeError(
            'Showcase customers already exist. Re-run with reset=True '
            '(management command: --reset) inside DEMO_MODE only.'
        )

    users = _seed_users()
    tipos = _seed_tipos()
    customers = _seed_customers(users, tipos)
    presentations = _seed_catalog()
    promotions = _seed_home_promotions(presentations)
    testimonials = _seed_home_testimonials()
    quotes = _seed_quotes(customers, presentations)
    orders = _seed_orders(customers, users, presentations)
    _seed_quickbooks_mock()

    home = HomeContenido.objects.order_by('-actualizado').first()
    if home is None:
        home = HomeContenido(activo=True)
    apply_demo_home_contenido(home, save=True)
    _clear_home_marketing_caches()

    from config.ai_assistant.models import AssistantConfiguration
    from config.ai_assistant.services.demo_assistant import apply_demo_assistant_config

    assistant = AssistantConfiguration.get_solo()
    apply_demo_assistant_config(assistant, save=True)

    return {
        'users': {key: value.username for key, value in users.items()},
        'demo_login': f'demo@{DEMO_EMAIL_DOMAIN}',
        'demo_password': DEMO_PASSWORD,
        'customers': len(customers),
        'presentations': len(presentations),
        'promotions': len(promotions),
        'testimonials': len(testimonials),
        'quotes': len(quotes),
        'orders': len(orders),
        'invoices': Invoice.objects.count(),
        'deliveries': Delivery.objects.count(),
        'qb_sync_runs': QuickBooksSyncRun.objects.count(),
        'assistant': assistant.assistant_name,
    }
