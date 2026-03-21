from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Cliente

@receiver(post_save, sender=Cliente)
def activar_usuario_si_aprobado(sender, instance, **kwargs):

    if instance.aprobado:
        usuario = instance.usuario
        usuario.is_active = True
        usuario.save()