from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AuditoriaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'config.auditoria'
    verbose_name = _('Audit trail')
