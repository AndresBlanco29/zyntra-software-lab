#!/usr/bin/env python
import os
import sys
import django

# Asegurar que la raíz del proyecto esté en sys.path (como hace manage.py)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Ajustar settings como en manage.py
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')

try:
    django.setup()
except Exception as e:
    print('Error al inicializar Django:', e)
    sys.exit(2)

from django.contrib.auth import get_user_model
from config.usuarios.permissions import get_effective_permissions

username = 'blanca'
password = '123'

User = get_user_model()
try:
    u = User.objects.get(username=username)
except User.DoesNotExist:
    print(f"Usuario '{username}' no encontrado.")
    sys.exit(1)

u.role = 'backoffice'
# set_password hashará la contraseña
u.set_password(password)
u.save()

print(f"Usuario actualizado: {u.username}")
print(f"role={u.role}")
perms = get_effective_permissions(u)
print('Permisos efectivos:', sorted(perms))
print('Hecho.')
