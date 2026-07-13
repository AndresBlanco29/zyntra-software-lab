from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from config.clientes.models import Cliente
from config.usuarios.models import Usuario


class Command(BaseCommand):
	help = (
		'Resolve duplicate Quik Stop logins: keep "quikstop" (no C) as the only '
		'sign-in username and permanently retire "quickstop" (with C).'
	)

	def add_arguments(self, parser):
		parser.add_argument('--keep', default='quikstop', help='Login username to keep (default: quikstop).')
		parser.add_argument('--retire', default='quickstop', help='Login username to retire (default: quickstop).')
		parser.add_argument(
			'--strategy',
			choices=('auto', 'keep-account', 'keep-customer-from-retire'),
			default='auto',
			help=(
				'keep-account: keep the --keep user row and retire the other. '
				'keep-customer-from-retire: keep the customer linked to --retire '
				'(usually the real ATL store) but rename its login to --keep. '
				'auto: choose from company name / order activity.'
			),
		)
		parser.add_argument(
			'--apply',
			action='store_true',
			help='Apply changes. Without this flag the command only prints a report.',
		)

	def handle(self, *args, **options):
		keep_username = str(options['keep'] or '').strip().lower()
		retire_username = str(options['retire'] or '').strip().lower()
		strategy = options['strategy']
		apply_changes = bool(options['apply'])

		if not keep_username or not retire_username:
			raise CommandError('Both --keep and --retire usernames are required.')
		if keep_username == retire_username:
			raise CommandError('--keep and --retire must be different usernames.')

		keep_user = self._find_user(keep_username)
		retire_user = self._find_user(retire_username)

		self.stdout.write('=== Duplicate login cleanup ===')
		self._print_user_report('KEEP CANDIDATE', keep_user, keep_username)
		self._print_user_report('RETIRE CANDIDATE', retire_user, retire_username)

		if keep_user is None and retire_user is None:
			raise CommandError('Neither username was found.')
		if keep_user is None:
			raise CommandError(
				f'Keep username "{keep_username}" was not found. '
				'If the real store is still on quickstop, re-run with '
				'--strategy keep-customer-from-retire after creating quikstop, '
				'or first free/create that login.'
			)
		if retire_user is None:
			self.stdout.write(self.style.WARNING(
				f'Retire username "{retire_username}" was not found. Nothing to change.'
			))
			return
		if keep_user.pk == retire_user.pk:
			raise CommandError('Keep and retire resolved to the same user row.')

		if strategy == 'auto':
			strategy = self._choose_strategy(keep_user, retire_user)
			self.stdout.write(f'\nAuto-selected strategy: {strategy}')

		if strategy == 'keep-account':
			plan = (
				f'1) Rename/deactivate user "{retire_user.username}"\n'
				f'2) Ensure user "{keep_user.username}" stays active as "{keep_username}"'
			)
		else:
			plan = (
				f'1) Move login "{keep_username}" onto customer of user #{retire_user.pk} '
				f'({getattr(getattr(retire_user, "cliente", None), "nombre_empresa", None)!r})\n'
				f'2) Deactivate the other user account #{keep_user.pk} '
				f'({getattr(getattr(keep_user, "cliente", None), "nombre_empresa", None)!r})'
			)

		self.stdout.write('\nPlanned actions:')
		self.stdout.write(plan)

		if not apply_changes:
			self.stdout.write(self.style.WARNING(
				'\nDry run only. Re-run with --apply to make changes.'
			))
			return

		with transaction.atomic():
			if strategy == 'keep-account':
				self._retire_user(retire_user, retire_username)
				self._ensure_active_username(keep_user, keep_username)
				active_user = keep_user
			else:
				# Real customer is currently on the "quickstop" user row.
				# Free the keep username, then rename the real customer login.
				self._retire_user(keep_user, keep_username)
				self._ensure_active_username(retire_user, keep_username)
				active_user = retire_user

			active_user.refresh_from_db()

		self.stdout.write(self.style.SUCCESS(
			f'\nDone. The only active login is "{active_user.username}" '
			f'(user_id={active_user.pk}).'
		))

	def _choose_strategy(self, keep_user, retire_user):
		keep_cliente = getattr(keep_user, 'cliente', None)
		retire_cliente = getattr(retire_user, 'cliente', None)
		keep_score = self._customer_score(keep_cliente)
		retire_score = self._customer_score(retire_cliente)
		self.stdout.write(f'\nCustomer activity score keep={keep_score} retire={retire_score}')
		# If the account with the typo owns the real store activity, rename that
		# account to quikstop instead of keeping an empty/other quikstop row.
		if retire_score > keep_score:
			return 'keep-customer-from-retire'
		return 'keep-account'

	def _customer_score(self, cliente):
		if cliente is None:
			return -1
		counts = (
			Cliente.objects
			.filter(pk=cliente.pk)
			.annotate(
				pedido_count=Count('pedidos', distinct=True),
				invoice_count=Count('invoices', distinct=True),
			)
			.values('pedido_count', 'invoice_count')
			.first()
		) or {}
		name = (cliente.nombre_empresa or '').upper()
		name_bonus = 0
		if 'QUIK STOP' in name or 'QUICK STOP' in name or 'QUIKSTOP' in name or 'QUICKSTOP' in name:
			name_bonus = 100
		if 'ATL' in name:
			name_bonus += 20
		return name_bonus + (counts.get('pedido_count') or 0) * 10 + (counts.get('invoice_count') or 0) * 10

	def _find_user(self, username):
		return (
			Usuario.objects
			.filter(username__iexact=username)
			.select_related('cliente')
			.first()
		)

	def _print_user_report(self, label, user, expected_username):
		self.stdout.write(f'\n[{label}] looking for username={expected_username!r}')
		if user is None:
			self.stdout.write('  (not found)')
			return

		cliente = getattr(user, 'cliente', None)
		pedido_count = 0
		invoice_count = 0
		if cliente is not None:
			counts = (
				Cliente.objects
				.filter(pk=cliente.pk)
				.annotate(
					pedido_count=Count('pedidos', distinct=True),
					invoice_count=Count('invoices', distinct=True),
				)
				.values('pedido_count', 'invoice_count')
				.first()
			) or {}
			pedido_count = counts.get('pedido_count', 0)
			invoice_count = counts.get('invoice_count', 0)

		self.stdout.write(
			f'  user_id={user.pk} username={user.username!r} role={user.role} '
			f'active={user.is_active} usable_password={user.has_usable_password()} '
			f'email={user.email!r}'
		)
		if cliente is None:
			self.stdout.write('  cliente=(none)')
		else:
			self.stdout.write(
				f'  cliente_id={cliente.pk} empresa={cliente.nombre_empresa!r} '
				f'phone={cliente.telefono!r} aprobado={cliente.aprobado} '
				f'pedidos={pedido_count} invoices={invoice_count}'
			)

	def _unique_released_username(self, user, original_username):
		base = f'released-{user.pk}-{original_username}'[:140]
		candidate = base
		suffix = 2
		while Usuario.objects.filter(username__iexact=candidate).exclude(pk=user.pk).exists():
			candidate = f'{base}-{suffix}'[:150]
			suffix += 1
		return candidate

	def _retire_user(self, user, original_username):
		released_username = self._unique_released_username(user, original_username)
		user.username = released_username
		user.is_active = False
		user.set_unusable_password()
		user.save(update_fields=['username', 'is_active', 'password'])
		self.stdout.write(f'Retired user_id={user.pk} -> {released_username!r} (inactive)')

	def _ensure_active_username(self, user, username):
		user.username = username
		user.is_active = True
		user.save(update_fields=['username', 'is_active'])
		self.stdout.write(f'Active login user_id={user.pk} -> {username!r}')
