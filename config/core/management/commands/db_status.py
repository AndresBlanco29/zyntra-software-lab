from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection


class Command(BaseCommand):
    help = 'Muestra qué base de datos usa Django y prueba la conexión.'

    def handle(self, *args, **options):
        db = settings.DATABASES['default']
        engine = db.get('ENGINE', '')
        name = db.get('NAME', '')

        if 'sqlite' in engine:
            self.stdout.write(self.style.WARNING('Motor: SQLite (no hay MySQL en .env)'))
            self.stdout.write(f'Archivo: {name}')
            self.stdout.write(
                'Para MySQL Workbench, define MYSQLDATABASE, MYSQLUSER, MYSQLHOST y MYSQLPASSWORD en .env'
            )
            return

        self.stdout.write(self.style.SUCCESS('Motor: MySQL'))
        self.stdout.write(f"Host: {db.get('HOST')}:{db.get('PORT')}")
        self.stdout.write(f"Base: {name}")
        self.stdout.write(f"Usuario: {db.get('USER')}")

        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
            self.stdout.write(self.style.SUCCESS('Conexión: OK'))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'Conexión: FALLO — {exc}'))
