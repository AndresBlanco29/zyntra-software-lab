from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario

@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):

    list_display = (
        'username',
        'email',
        'get_grupos',
        'telefono',
        'documento',
        'is_active',
    )

    def get_grupos(self, obj):
        return ", ".join([g.name for g in obj.groups.all()])

    get_grupos.short_description = "Roles"