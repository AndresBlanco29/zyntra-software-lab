from django.core.management.base import BaseCommand, CommandError
from django.test.client import RequestFactory

from config.reportes.views import _build_dashboard_context, _collect_report_data, _parse_filters, _parse_range, send_reports_email


class Command(BaseCommand):
	help = 'Send the daily reports summary by email to admins and report recipients.'

	def add_arguments(self, parser):
		parser.add_argument('--section', default='all', help='Specific report section to send. Defaults to all.')
		parser.add_argument('--emails', nargs='*', default=None, help='Optional explicit recipient list.')

	def handle(self, *args, **options):
		factory = RequestFactory()
		request = factory.get('/reportes/', {'period': 'today', 'section': options.get('section') or 'all'})
		period = _parse_range(request)
		filters = _parse_filters(request)
		report_data = _build_dashboard_context(request, period, _collect_report_data(period, filters=filters), filters=filters)
		recipients = send_reports_email(
			period=period,
			report_data=report_data,
			section=filters['section'],
			recipient_emails=options.get('emails') or None,
		)
		if not recipients:
			raise CommandError('No recipients are configured for daily reports.')
		self.stdout.write(self.style.SUCCESS(f'Daily reports email sent to {len(recipients)} recipients.'))