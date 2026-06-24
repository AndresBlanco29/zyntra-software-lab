from dataclasses import dataclass
from decimal import Decimal

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
	request_amount: Decimal
	projected_balance: Decimal
	remaining_limit: Decimal
	excess_amount: Decimal

	@property
	def has_limit(self):
		return self.configured and self.credit_limit > 0


def evaluate_customer_credit_limit(*, cliente, additional_amount):
	additional_amount = _quantize_money(additional_amount)
	credit_limit = cliente.credit_limit
	if credit_limit is None:
		return CreditLimitEvaluation(
			configured=False,
			exceeds_limit=False,
			credit_limit=Decimal('0.00'),
			due_balance=cliente.due_balance,
			request_amount=additional_amount,
			projected_balance=_quantize_money(cliente.due_balance + additional_amount),
			remaining_limit=Decimal('0.00'),
			excess_amount=Decimal('0.00'),
		)

	credit_limit = _quantize_money(credit_limit)
	due_balance = _quantize_money(cliente.due_balance)
	projected_balance = _quantize_money(due_balance + additional_amount)
	remaining_limit = _quantize_money(max(credit_limit - due_balance, Decimal('0.00')))
	excess_amount = _quantize_money(max(projected_balance - credit_limit, Decimal('0.00')))
	return CreditLimitEvaluation(
		configured=True,
		exceeds_limit=excess_amount > 0,
		credit_limit=credit_limit,
		due_balance=due_balance,
		request_amount=additional_amount,
		projected_balance=projected_balance,
		remaining_limit=remaining_limit,
		excess_amount=excess_amount,
	)


def build_credit_limit_alert_message(*, evaluation, cliente_nombre, pedido_id=None):
	parts = [
		_('Customer %(customer)s would exceed the configured credit limit.') % {'customer': cliente_nombre},
		_('Credit limit: $%(limit)s') % {'limit': evaluation.credit_limit},
		_('Current due balance: $%(due)s') % {'due': evaluation.due_balance},
		_('This order/invoice amount: $%(amount)s') % {'amount': evaluation.request_amount},
		_('Projected due balance: $%(projected)s') % {'projected': evaluation.projected_balance},
		_('Remaining limit before this operation: $%(remaining)s') % {'remaining': evaluation.remaining_limit},
		_('Exceeded by: $%(excess)s') % {'excess': evaluation.excess_amount},
	]
	if pedido_id:
		parts.insert(1, _('Sales order #%(order)s') % {'order': pedido_id})
	return ' '.join(str(part) for part in parts)


def create_credit_limit_alert(*, cliente, pedido, evaluation):
	from config.clientes.models import ClienteCreditoLimiteAlerta

	ClienteCreditoLimiteAlerta.objects.filter(
		cliente=cliente,
		pedido=pedido,
		estado=ClienteCreditoLimiteAlerta.ESTADO_PENDIENTE,
	).update(estado=ClienteCreditoLimiteAlerta.ESTADO_BLOQUEADO)

	return ClienteCreditoLimiteAlerta.objects.create(
		cliente=cliente,
		pedido=pedido,
		monto_adeudado=evaluation.due_balance,
		monto_operacion=evaluation.request_amount,
		limite_credito=evaluation.credit_limit,
		exceso=evaluation.excess_amount,
	)


def notify_credit_limit_alert(*, alerta, pedido_id):
	from django.urls import reverse

	from config.notificaciones.models import crear_notificacion_backoffice

	cliente_nombre = alerta.cliente.nombre_empresa
	crear_notificacion_backoffice(
		titulo=_('Credit limit exceeded for %(customer)s') % {'customer': cliente_nombre},
		mensaje=build_credit_limit_alert_message(
			evaluation=CreditLimitEvaluation(
				configured=True,
				exceeds_limit=True,
				credit_limit=alerta.limite_credito,
				due_balance=alerta.monto_adeudado,
				request_amount=alerta.monto_operacion,
				projected_balance=_quantize_money(alerta.monto_adeudado + alerta.monto_operacion),
				remaining_limit=_quantize_money(max(alerta.limite_credito - alerta.monto_adeudado, Decimal('0.00'))),
				excess_amount=alerta.exceso,
			),
			cliente_nombre=cliente_nombre,
			pedido_id=pedido_id,
		),
		tipo='PEDIDO',
		url=reverse('backoffice_pedido_detalle', args=[pedido_id]),
	)


def validate_credit_limit_for_pedido_invoice(*, pedido, request_amount):
	evaluation = evaluate_customer_credit_limit(cliente=pedido.cliente, additional_amount=request_amount)
	if not evaluation.exceeds_limit or pedido.credit_limit_liberado:
		return evaluation
	if pedido.credit_limit_bloqueado:
		raise CreditLimitBlockedError(evaluation)
	raise CreditLimitExceededError(evaluation)


class CreditLimitExceededError(Exception):
	def __init__(self, evaluation):
		self.evaluation = evaluation
		super().__init__('credit_limit_exceeded')


class CreditLimitBlockedError(Exception):
	def __init__(self, evaluation):
		self.evaluation = evaluation
		super().__init__('credit_limit_blocked')


def resolve_credit_limit_alert(*, pedido, usuario, action):
	from django.utils import timezone

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
	if action == 'release':
		pedido.credit_limit_liberado = True
		pedido.credit_limit_bloqueado = False
		pedido.save(update_fields=['credit_limit_liberado', 'credit_limit_bloqueado', 'actualizada_en'])
		alerta.estado = ClienteCreditoLimiteAlerta.ESTADO_LIBERADO
	elif action == 'block':
		pedido.credit_limit_liberado = False
		pedido.credit_limit_bloqueado = True
		pedido.save(update_fields=['credit_limit_liberado', 'credit_limit_bloqueado', 'actualizada_en'])
		cliente = pedido.cliente
		cliente.credit_hold = True
		cliente.save(update_fields=['credit_hold'])
		alerta.estado = ClienteCreditoLimiteAlerta.ESTADO_BLOQUEADO
	else:
		raise ValueError('invalid_action')

	alerta.resuelto_por = usuario
	alerta.resuelto_en = now
	alerta.save(update_fields=['estado', 'resuelto_por', 'resuelto_en'])
	return alerta
