#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from config.usuarios.models import Usuario

# Crear usuario admin
try:
    admin_user = Usuario.objects.create_user(
        username='vane',
        email='vane@admin.local',
        password='123',
        role='admin',
        is_superuser=True,
        is_staff=True,
        first_name='Vane',
        last_name='Admin'
    )
    print(f"✓ Usuario admin 'vane' creado exitosamente")
    print(f"  - Username: vane")
    print(f"  - Contraseña: 123")
    print(f"  - Role: admin")
    print(f"  - is_superuser: {admin_user.is_superuser}")
    print(f"  - is_staff: {admin_user.is_staff}")
except Exception as e:
    print(f"✗ Error al crear usuario: {e}")

# Verificar que el usuario fue creado
try:
    vane = Usuario.objects.get(username='vane')
    print(f"\n✓ Verificación: Usuario 'vane' encontrado en BD")
except Usuario.DoesNotExist:
    print(f"\n✗ Usuario 'vane' no fue creado")
