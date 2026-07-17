from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _


def _quantize_money(value):
	amount = Decimal(str(value or '0.00'))
	return amount.quantize(Decimal('0.01'))


@dataclass(frozen=True)
class CreditLimitEvaluation:
	configured: bool
	exceeds_limit: bool
	credit_limit: Decimal
	due_balance: Decimal
	open_balance: Decimal
	request_amount: Decimal
	projected_balance: Decimal
	remaining_limit: Decimal
	excess_amount: Decimal

	@property
	def has_limit(self):
		return self.configured and self.credit_limit > 0


def evaluate_customer_credit_limit(*, cliente, additional_amount):
	additional_amount = _quantize_money(additional_amount)
	from config.clientes.balance_summary import build_customer_balance_summary

	summary = build_customer_balance_summary(cliente)
	credit_limit = cliente.credit_limit
	open_balance = _quantize_money(summary.total_open_balance)
	due_balance = _quantize_money(summary.overdue_balance)
	if credit_limit is None:
		return CreditLimitEvaluation(
			configured=False,
			exceeds_limit=False,
			credit_limit=Decimal('0.00'),
			due_balance=due_balance,
			open_balance=open_balance,
			request_amount=additional_amount,
			projected_balance=_quantize_money(open_balance + additional_amount),
			remaining_limit=Decimal('0.00'),
			excess_amount=Decimal('0.00'),
		)

	credit_limit = _quantize_money(credit_limit)
	projected_balance = _quantize_money(open_balance + additional_amount)
	remaining_limit = _quantize_money(max(credit_limit - open_balance, Decimal('0.00')))
	excess_amount = _quantize_money(max(projected_balance - credit_limit, Decimal('0.00')))
	return CreditLimitEvaluation(
		configured=True,
		exceeds_limit=excess_amount > 0,
		credit_limit=credit_limit,
		due_balance=due_balance,
		open_balance=open_balance,
		request_amount=additional_amount,
		projected_balance=projected_balance,
		remaining_limit=remaining_limit,
		excess_amount=excess_amount,
	)


def build_credit_limit_alert_message(*, evaluation, cliente_nombre, pedido_id=None):
	parts = [
		_('Customer %(customer)s would exceed the configured credit limit.') % {'customer': cliente_nombre},
		_('Credit limit: $%(limit)s') % {'limit': evaluation.credit_limit},
		_('Current open balance: $%(open)s') % {'open': evaluation.open_balance},
		_('This order/invoice amount: $%(amount)s') % {'amount': evaluation.request_amount},
		_('Projected open balance: $%(projected)s') % {'projected': evaluation.projected_balance},
		_('Remaining limit before this operation: $%(remaining)s') % {'remaining': evaluation.remaining_limit},
		_('Exceeded by: $%(excess)s') % {'excess': evaluation.excess_amount},
	]
	if pedido_id:
		parts.insert(1, _('Sales order #%(order)s') % {'order': pedido_id})
	return ' '.join(str(part) for part in parts)


def pedido_tiene_credit_hold_pendiente(pedido):
	"""True when the order cannot continue until credit hold is released."""
	if getattr(pedido, 'credit_limit_liberado', False):
		return False
	if getattr(pedido, 'credit_limit_bloqueado', False):
		return True
	from config.clientes.models import ClienteCreditoLimiteAlerta

	return ClienteCreditoLimiteAlerta.objects.filter(
		pedido_id=pedido.id,
		estado=ClienteCreditoLimiteAlerta.ESTADO_PENDIENTE,
	).exists()


def create_credit_limit_alert(*, cliente, pedido, evaluation):
	from config.clientes.models import ClienteCreditoLimiteAlerta

	pending = (
		ClienteCreditoLimiteAlerta.objects.filter(
			cliente=cliente,
			pedido=pedido,
			estado=ClienteCreditoLimiteAlerta.ESTADO_PENDIENTE,
		)
		.order_by('-creado_en')
		.first()
	)
	if pending is not None:
		pending.monto_adeudado = evaluation.open_balance
		pending.monto_operacion = evaluation.request_amount
		pending.limite_credito = evaluation.credit_limit
		pending.exceso = evaluation.excess_amount
		pending.save(
			update_fields=['monto_adeudado', 'monto_operacion', 'limite_credito', 'exceso']
		)
		return pending

	return ClienteCreditoLimiteAlerta.objects.create(
		cliente=cliente,
		pedido=pedido,
		monto_adeudado=evaluation.open_balance,
		monto_operacion=evaluation.request_amount,
		limite_credito=evaluation.credit_limit,
		exceso=evaluation.excess_amount,
	)


def credit_hold_email_recipients():
	"""Prefer test inbox during validation; otherwise admin/backoffice emails."""
	test_email = (getattr(settings, 'CREDIT_HOLD_TEST_EMAIL', '') or '').strip()
	if test_email:
		return [test_email]

	from django.contrib.auth import get_user_model

	user_model = get_user_model()
	return list(
		user_model.objects.filter(role__in=['admin', 'backoffice'], is_active=True)
		.exclude(email='')
		.values_list('email', flat=True)
		.distinct()
	)


def send_credit_hold_email(*, alerta, pedido, evaluation):
	from django.urls import reverse

	from config.core.email_branding import brand_email_context, get_app_base_url

	recipients = credit_hold_email_recipients()
	if not recipients:
		return False

	order_url = f"{get_app_base_url()}{reverse('backoffice_pedido_detalle', args=[pedido.id])}"
	html_content = render_to_string(
		'emails/credit_hold_alert.html',
		{
			'pedido': pedido,
			'cliente': pedido.cliente,
			'alerta': alerta,
			'evaluation': evaluation,
			'order_url': order_url,
			'reason_label': str(_('Credit Limit Exceeded')),
			**brand_email_context(),
		},
	)
	text_content = build_credit_limit_alert_message(
		evaluation=evaluation,
		cliente_nombre=pedido.cliente.nombre_empresa,
		pedido_id=pedido.id,
	)
	email = EmailMultiAlternatives(
		subject=_('CREDIT HOLD — Order #%(id)s — %(customer)s') % {
			'id': pedido.id,
			'customer': pedido.cliente.nombre_empresa,
		},
		body=text_content,
		from_email=settings.DEFAULT_FROM_EMAIL or settings.SERVER_EMAIL,
		to=recipients,
	)
	email.attach_alternative(html_content, 'text/html')
	email.send(fail_silently=False)
	return True


def notify_credit_limit_alert(*, alerta, pedido_id, evaluation=None, send_email=True):
	from django.urls import reverse

	from config.notificaciones.models import crear_notificacion_backoffice

	cliente_nombre = alerta.cliente.nombre_empresa
	if evaluation is None:
		evaluation = CreditLimitEvaluation(
			configured=True,
			exceeds_limit=True,
			credit_limit=alerta.limite_credito,
			due_balance=alerta.monto_adeudado,
			open_balance=alerta.monto_adeudado,
			request_amount=alerta.monto_operacion,
			projected_balance=_quantize_money(alerta.monto_adeudado + alerta.monto_operacion),
			remaining_limit=_quantize_money(max(alerta.limite_credito - alerta.monto_adeudado, Decimal('0.00'))),
			excess_amount=alerta.exceso,
		)

	crear_notificacion_backoffice(
		titulo=_('CREDIT HOLD — Order #%(order)s') % {'order': pedido_id},
		mensaje=build_credit_limit_alert_message(
			evaluation=evaluation,
			cliente_nombre=cliente_nombre,
			pedido_id=pedido_id,
		),
		tipo='PEDIDO',
		url=reverse('backoffice_pedido_detalle', args=[pedido_id]),
	)

	if send_email and alerta.pedido_id:
		try:
			send_credit_hold_email(alerta=alerta, pedido=alerta.pedido, evaluation=evaluation)
		except Exception:
			# Never block order creation because email delivery failed.
			import logging
			logging.getLogger(__name__).exception('Credit hold email failed for order #%s', pedido_id)


def apply_credit_hold_on_order_arrival(*, pedido, request=None):
	"""Place a new order on credit hold when the customer would exceed their limit."""
	if getattr(pedido, 'credit_limit_liberado', False):
		return None

	evaluation = evaluate_customer_credit_limit(
		cliente=pedido.cliente,
		additional_amount=pedido.total,
	)
	if not evaluation.exceeds_limit:
		return None

	alerta = create_credit_limit_alert(
		cliente=pedido.cliente,
		pedido=pedido,
		evaluation=evaluation,
	)
	# Ensure relation is available for email templates.
	if alerta.pedido_id and getattr(alerta, 'pedido', None) is None:
		alerta.pedido = pedido

	notify_credit_limit_alert(
		alerta=alerta,
		pedido_id=pedido.id,
		evaluation=evaluation,
		send_email=True,
	)

	from config.auditoria.business_events import log_business_event
	from config.auditoria.models import AuditLog

	log_business_event(
		getattr(request, 'user', None) if request is not None else None,
		action_label=_('Credit hold placed on order #%(id)s — credit limit exceeded') % {
			'id': pedido.id,
		},
		action_category=AuditLog.CATEGORY_ACTION,
		entity_type='Pedido',
		entity_id=str(pedido.id),
		entity_label=_('Order #%(id)s - %(client)s') % {
			'id': pedido.id,
			'client': pedido.cliente.nombre_empresa,
		},
		metadata={
			'reason': 'CREDIT_LIMIT_EXCEEDED',
			'credit_limit': str(evaluation.credit_limit),
			'open_balance': str(evaluation.open_balance),
			'order_amount': str(evaluation.request_amount),
			'projected_balance': str(evaluation.projected_balance),
			'excess_amount': str(evaluation.excess_amount),
			'alerta_id': alerta.id,
		},
		changes=[
			{
				'field': str(_('Credit hold')),
				'before': str(_('None')),
				'after': str(_('HOLD — Credit Limit Exceeded')),
			},
		],
		request=request,
		module='Orders',
	)
	return alerta


def validate_credit_limit_for_pedido_invoice(*, pedido, request_amount):
	evaluation = evaluate_customer_credit_limit(cliente=pedido.cliente, additional_amount=request_amount)
	if pedido.credit_limit_liberado:
		return evaluation
	if pedido.credit_limit_bloqueado:
		raise CreditLimitBlockedError(evaluation)
	if pedido_tiene_credit_hold_pendiente(pedido):
		# Keep the early hold pending; do not invent a second alert lifecycle.
		if not evaluation.exceeds_limit:
			# Re-evaluate with order total so Release/Block UI still has numbers.
			evaluation = evaluate_customer_credit_limit(
				cliente=pedido.cliente,
				additional_amount=pedido.total,
			)
		raise CreditLimitExceededError(evaluation)
	if not evaluation.exceeds_limit:
		return evaluation
	raise CreditLimitExceededError(evaluation)


class CreditLimitExceededError(Exception):
	def __init__(self, evaluation):
		self.evaluation = evaluation
		super().__init__('credit_limit_exceeded')


class CreditLimitBlockedError(Exception):
	def __init__(self, evaluation):
		self.evaluation = evaluation
		super().__init__('credit_limit_blocked')


def resolve_credit_limit_alert(*, pedido, usuario, action, comentario=''):
	from django.utils import timezone

	from config.auditoria.business_events import log_business_event
	from config.auditoria.models import AuditLog
	from config.clientes.models import ClienteCreditoLimiteAlerta

	alerta = (
		ClienteCreditoLimiteAlerta.objects.select_for_update()
		.filter(pedido=pedido, estado=ClienteCreditoLimiteAlerta.ESTADO_PENDIENTE)
		.order_by('-creado_en')
		.first()
	)
	if alerta is None:
		raise ValueError('pending_alert_not_found')

	now = timezone.now()
	comentario = (comentario or '').strip()
	if action == 'release':
		pedido.credit_limit_liberado = True
		pedido.credit_limit_bloqueado = False
		pedido.save(update_fields=['credit_limit_liberado', 'credit_limit_bloqueado', 'actualizada_en'])
		alerta.estado = ClienteCreditoLimiteAlerta.ESTADO_LIBERADO
		action_label = _('Released credit hold for order #%(id)s') % {'id': pedido.id}
		after_label = str(_('RELEASE — Authorized to continue'))
	elif action == 'block':
		pedido.credit_limit_liberado = False
		pedido.credit_limit_bloqueado = True
		pedido.save(update_fields=['credit_limit_liberado', 'credit_limit_bloqueado', 'actualizada_en'])
		cliente = pedido.cliente
		cliente.credit_hold = True
		cliente.save(update_fields=['credit_hold'])
		alerta.estado = ClienteCreditoLimiteAlerta.ESTADO_BLOQUEADO
		action_label = _('Blocked order #%(id)s for credit limit') % {'id': pedido.id}
		after_label = str(_('BLOCK — Customer placed on credit hold'))
	else:
		raise ValueError('invalid_action')

	alerta.resuelto_por = usuario
	alerta.resuelto_en = now
	alerta.save(update_fields=['estado', 'resuelto_por', 'resuelto_en'])

	log_business_event(
		usuario,
		action_label=action_label,
		action_category=AuditLog.CATEGORY_ACTION,
		entity_type='Pedido',
		entity_id=str(pedido.id),
		entity_label=_('Order #%(id)s - %(client)s') % {
			'id': pedido.id,
			'client': pedido.cliente.nombre_empresa,
		},
		metadata={
			'action': action,
			'comentario': comentario,
			'alerta_id': alerta.id,
			'limite_credito': str(alerta.limite_credito),
			'exceso': str(alerta.exceso),
		},
		changes=[
			{
				'field': str(_('Credit hold')),
				'before': str(_('HOLD — Credit Limit Exceeded')),
				'after': after_label,
			},
			*([{
				'field': str(_('Comment')),
				'before': '',
				'after': comentario,
			}] if comentario else []),
		],
		module='Orders',
	)
	return alerta
