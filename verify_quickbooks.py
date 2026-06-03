from django.test import Client
from django.contrib.auth import get_user_model
from django.conf import settings
import sys
import django

django.setup()

# Temporary allow testserver
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

try:
    User = get_user_model()
    username = 'testadmin'
    password = 'password'
    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(username, 'admin@example.com', password)
    
    client = Client()
    client.login(username=username, password=password)
    
    response = client.get('/quickbooks/')
    print(f'STATUS_CODE: {response.status_code}')
    
    content = response.content.decode('utf-8')
    has_hero = 'qb-hero-status' in content
    print(f'HAS_HERO: {has_hero}')
    
    messages = [
        'Today everything looks under control',
        'QuickBooks is not connected yet',
        'You have'
    ]
    found_msg = any(msg in content for msg in messages)
    print(f'FOUND_MSG: {found_msg}')

except Exception as e:
    print(f'ERROR: {str(e)}')
    sys.exit(1)
