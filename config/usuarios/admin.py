from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario

@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):

    list_display = (
        'username',
        'email',
        'role',
        'telefono',
        'documento',
        'is_active',
        'is_staff'
    )

    fieldsets = UserAdmin.fieldsets + (
        ('Información adicional', {
            'fields': (
                'role',
                'telefono',
                'documento',
            )
        }
         ),
    )
