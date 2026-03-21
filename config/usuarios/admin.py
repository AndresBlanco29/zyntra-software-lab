from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):

    list_display = (
        'username',
        'email',
        'get_role_display',
        'telefono',
        'documento',
        'is_active',
    )

    fieldsets = UserAdmin.fieldsets + (
        ('Información adicional', {
            'fields': ('role', 'telefono', 'documento')
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Información adicional', {
            'fields': ('role', 'telefono', 'documento')
        }),
    )

    def get_role_display(self, obj):
        return obj.get_role_display()

    get_role_display.short_description = "Rol"