from django.apps import AppConfig

class ClientesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'config.clientes'

    def ready(self):
        import config.clientes.signals