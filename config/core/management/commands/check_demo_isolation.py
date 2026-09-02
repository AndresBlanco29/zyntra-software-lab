from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config.config.demo import build_demo_isolation_report


class Command(BaseCommand):
    help = 'Verify DEMO_MODE isolation guards (safe to run in any environment).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--require-demo',
            action='store_true',
            help='Exit with error unless DEMO_MODE is enabled.',
        )

    def handle(self, *args, **options):
        report = build_demo_isolation_report(settings)
        if options['require_demo'] and not report['demo_mode']:
            raise CommandError('DEMO_MODE is not enabled. Refusing to continue.')

        self.stdout.write(self.style.NOTICE('Demo isolation report'))
        for check in report['checks']:
            mark = 'OK' if check['ok'] else 'FAIL'
            style = self.style.SUCCESS if check['ok'] else self.style.ERROR
            self.stdout.write(style(f"  [{mark}] {check['id']}: {check['detail']}"))

        if report['demo_mode'] and not all(item['ok'] for item in report['checks']):
            raise CommandError('DEMO isolation checks failed. See docs/demo/ISOLATION_CHECKLIST.md')

        if report['demo_mode']:
            self.stdout.write(self.style.SUCCESS('DEMO_MODE isolation checks passed.'))
        else:
            self.stdout.write(
                self.style.WARNING(
                    'DEMO_MODE is off. Production posture — no DEMO guards enforced.'
                )
            )
