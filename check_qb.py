import os
import django
from django.core.management import call_command

from django.conf import settings
if not settings.configured:
    settings.configure(
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
            'django.contrib.sessions',
            'config.integrations',
        ],
        ROOT_URLCONF='config.urls',
        MIDDLEWARE=[
            'django.contrib.sessions.middleware.SessionMiddleware',
            'django.contrib.auth.middleware.AuthenticationMiddleware',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'APP_DIRS': True,
        }],
    )
django.setup()
call_command('migrate', verbosity=0)

from django.test import Client
from django.contrib.auth.models import User

client = Client()
admin = User.objects.create_superuser('admin_tmp', 'admin@test.com', 'pass123')

client.force_login(admin)
response = client.get('/quickbooks/', follow=True)

print(f'Status Code: {response.status_code}')
content = response.content.decode('utf-8')
print(f"Contains 'Today everything looks under control': {'Today everything looks under control' in content}")
print(f"Contains 'QuickBooks is not connected yet': {'QuickBooks is not connected yet' in content}")
print(f"Contains 'Open review queue': {'Open review queue' in content}")
