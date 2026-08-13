import base64
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from reportlab.platypus import Image

from config.clientes.models import Cliente
from config.core.pdf_branding import build_pdf_storage_image
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario

TINY_PNG = base64.b64decode(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pS7QAAAAASUVORK5CYII='
)


class BuildPdfStorageImageTests(TestCase):
	def test_returns_image_from_uploaded_file(self):
		uploaded = SimpleUploadedFile('product.png', TINY_PNG, content_type='image/png')
		producto = Producto.objects.create(
			nombre='Producto Con Foto',
			categoria=Categoria.objects.create(nombre='Cat PDF'),
			marca=Marca.objects.create(nombre='Marca PDF'),
			imagen=uploaded,
			activo=True,
		)
		image = build_pdf_storage_image(producto.imagen, max_width=44, max_height=44)
		self.assertIsInstance(image, Image)
		self.assertLessEqual(float(image.drawWidth), 44.0)
		self.assertLessEqual(float(image.drawHeight), 44.0)

	def test_returns_none_when_image_missing(self):
		producto = Producto.objects.create(
			nombre='Producto Sin Foto',
			categoria=Categoria.objects.create(nombre='Cat PDF 2'),
			marca=Marca.objects.create(nombre='Marca PDF 2'),
			activo=True,
		)
		self.assertIsNone(build_pdf_storage_image(producto.imagen))
		self.assertIsNone(build_pdf_storage_image(None))


@override_settings(
	EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
	TWILIO_ACCOUNT_SID='',
	TWILIO_AUTH_TOKEN='',
	TWILIO_SMS_FROM='',
	TWILIO_WHATSAPP_FROM='',
)
class QuotePdfProductImageTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(
			username='bo-quote-pdf',
			password='secret123',
			role='backoffice',
		)
		self.customer_user = Usuario.objects.create_user(
			username='cli-quote-pdf',
			password='secret123',
			role='cliente',
		)
		self.cliente = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Quote PDF',
			telefono='5550001111',
			direccion='1 Quote St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
			sales_tax_number='TX-QPDF',
			certificado_tax=SimpleUploadedFile('cert.txt', b'cert'),
			aprobado=True,
		)
		categoria = Categoria.objects.create(nombre='Cat Quote PDF')
		marca = Marca.objects.create(nombre='Marca Quote PDF')
		self.producto_con_foto = Producto.objects.create(
			nombre='Producto Con Imagen',
			categoria=categoria,
			marca=marca,
			imagen=SimpleUploadedFile('con-foto.png', TINY_PNG, content_type='image/png'),
			activo=True,
		)
		self.producto_sin_foto = Producto.objects.create(
			nombre='Producto Sin Imagen',
			categoria=categoria,
			marca=marca,
			activo=True,
		)
		self.pres_con_foto = Presentacion.objects.create(
			producto=self.producto_con_foto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='unidades',
			precio_1=Decimal('20.00'),
		)
		self.pres_sin_foto = Presentacion.objects.create(
			producto=self.producto_sin_foto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='unidades',
			precio_1=Decimal('15.00'),
		)

	def _create_quote(self, *presentaciones):
		cotizacion = Cotizacion.objects.create(
			cliente=self.cliente,
			estado='LISTA_PARA_CONFIRMACION',
			total=Decimal('0.00'),
		)
		total = Decimal('0.00')
		for presentacion in presentaciones:
			precio = presentacion.precio_1
			CotizacionItem.objects.create(
				cotizacion=cotizacion,
				presentacion=presentacion,
				cantidad=1,
				precio=precio,
				subtotal=precio,
			)
			total += precio
		cotizacion.total = total
		cotizacion.save(update_fields=['total'])
		return cotizacion

	def test_quote_pdf_with_product_image_embeds_xobject(self):
		cotizacion = self._create_quote(self.pres_con_foto)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_cotizacion_pdf', args=[cotizacion.id]))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertIn(f'Quote-{cotizacion.id}.pdf', response['Content-Disposition'])
		pdf_bytes = response.content
		self.assertTrue(pdf_bytes.startswith(b'%PDF'))
		self.assertIn(b'/XObject', pdf_bytes)

	def test_quote_pdf_without_product_image_still_generates(self):
		cotizacion = self._create_quote(self.pres_sin_foto)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_cotizacion_pdf', args=[cotizacion.id]))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertTrue(response.content.startswith(b'%PDF'))
		# Brand logo may add XObject; ensure the download still succeeds without product photo.
		self.assertGreater(len(response.content), 500)
