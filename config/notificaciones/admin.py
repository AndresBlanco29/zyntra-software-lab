from django.contrib import admin

from .models import Notificacion


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
	list_display = ('titulo', 'tipo', 'usuario', 'leida', 'creada_en')
	list_filter = ('tipo', 'leida', 'creada_en')
	search_fields = ('titulo', 'mensaje')
