from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q


class Notificacion(models.Model):

	TIPO_CHOICES = (
		('COTIZACION', 'Cotizacion'),
		('PEDIDO', 'Pedido'),
		('NOTA_AJUSTE', 'Nota de ajuste'),
		('CLIENTE', 'Solicitud de cliente'),
	)

	tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
	titulo = models.CharField(max_length=160)
	mensaje = models.TextField(blank=True)
	url = models.CharField(max_length=300, blank=True)
	leida = models.BooleanField(default=False)
	creada_en = models.DateTimeField(auto_now_add=True)
	usuario = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='notificaciones',
	)

	class Meta:
		ordering = ('-creada_en',)

	def __str__(self):
		return self.titulo

	@property
	def destino(self):
		return 'BACKOFFICE'


class WorkspaceDispatchAlertReadState(models.Model):
	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name='dispatch_alert_read_state',
	)
	last_opened_at = models.DateTimeField(blank=True, null=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		db_table = 'notificaciones_dispatch_alert_read_state'

	def __str__(self):
		return f'Dispatch alerts read state for {self.user_id}'


class WorkspaceCustomerRequestAlertReadState(models.Model):
	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name='customer_request_alert_read_state',
	)
	last_opened_at = models.DateTimeField(blank=True, null=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		db_table = 'notificaciones_customer_request_alert_read_state'

	def __str__(self):
		return f'Customer request alerts read state for {self.user_id}'


def _get_fallback_notification_user():
	User = get_user_model()
	return User.objects.filter(is_active=True).filter(
		Q(is_superuser=True) | ~Q(role='cliente')
	).order_by('-is_superuser', 'id').first()


def crear_notificacion_backoffice(titulo, mensaje, tipo, url='', usuario=None):
	return Notificacion.objects.create(
		tipo=tipo,
		titulo=titulo,
		mensaje=mensaje,
		url=url,
		usuario=usuario or _get_fallback_notification_user(),
	)


def crear_notificacion_usuario(*, usuario, titulo, mensaje, tipo, url=''):
	return Notificacion.objects.create(
		tipo=tipo,
		titulo=titulo,
		mensaje=mensaje,
		url=url,
		usuario=usuario,
	)
