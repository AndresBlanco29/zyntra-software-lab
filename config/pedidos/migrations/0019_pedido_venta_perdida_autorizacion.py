from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pedidos', '0018_realign_reserved_inventory_on_open_orders'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='venta_perdida_autorizada',
            field=models.BooleanField(
                default=False,
                help_text='Supervisor authorized selling one or more lines below cost on this order.',
            ),
        ),
        migrations.AddField(
            model_name='pedido',
            name='venta_perdida_autorizado_por',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='pedido',
            name='venta_perdida_autorizada_por_user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pedidos_venta_perdida_autorizados',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='pedido',
            name='venta_perdida_comentario',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='pedido',
            name='venta_perdida_autorizada_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
