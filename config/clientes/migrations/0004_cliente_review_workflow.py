import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def populate_review_status(apps, schema_editor):
    Cliente = apps.get_model('clientes', 'Cliente')

    Cliente.objects.filter(aprobado=True).update(estado_revision='APROBADO')
    Cliente.objects.filter(aprobado=False).update(estado_revision='PENDIENTE')

    for cliente in Cliente.objects.filter(correction_token__isnull=True).iterator():
        cliente.correction_token = uuid.uuid4()
        cliente.save(update_fields=['correction_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0003_cliente_declaracion_fiscal'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='adjunto_rechazo',
            field=models.FileField(blank=True, null=True, upload_to='certificados/rechazos/'),
        ),
        migrations.AddField(
            model_name='cliente',
            name='aprobado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='aprobado_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clientes_aprobados_admin', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='cliente',
            name='corrected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='correction_requested_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='correction_token',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='estado_revision',
            field=models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('APROBADO', 'Aprobado'), ('RECHAZADO', 'Rechazado')], db_index=True, default='PENDIENTE', max_length=20),
        ),
        migrations.AddField(
            model_name='cliente',
            name='nota_rechazo',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='cliente',
            name='rechazado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='rechazado_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clientes_rechazados_admin', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(populate_review_status, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='cliente',
            name='correction_token',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]