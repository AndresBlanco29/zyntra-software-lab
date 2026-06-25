from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

from config.core.migration_utils import separate_create_model, wrap_add_field_operations
from config.facturacion.models import FacturacionRegistroAnulacion

CREATE_REGISTRO_ANULACION = migrations.CreateModel(
    name='FacturacionRegistroAnulacion',
    fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('tipo_documento', models.CharField(choices=[('INVOICE', 'Invoice'), ('NOTA_CREDITO', 'Credit note'), ('NOTA_DEBITO', 'Debit note')], db_index=True, max_length=20)),
        ('numero_documento', models.CharField(db_index=True, max_length=30)),
        ('documento_id', models.PositiveIntegerField(db_index=True)),
        ('motivo', models.TextField(blank=True)),
        ('snapshot', models.JSONField(blank=True, default=dict)),
        ('anulado_en', models.DateTimeField(auto_now_add=True, db_index=True)),
        ('anulado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registros_anulacion_facturacion', to=settings.AUTH_USER_MODEL)),
        ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='registros_anulacion_facturacion', to='clientes.cliente')),
        ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registros_anulacion', to='facturacion.invoice')),
        ('nota', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registros_anulacion', to='facturacion.notaajuste')),
    ],
    options={
        'verbose_name': 'Voided document record',
        'verbose_name_plural': 'Voided document records',
        'ordering': ('-anulado_en', '-id'),
    },
)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('clientes', '0010_cliente_credit_limit'),
        ('facturacion', '0020_invoiceitem_line_discount'),
    ]

    operations = [
        *wrap_add_field_operations('facturacion', [
            migrations.AddField(
                model_name='invoice',
                name='anulada_en',
                field=models.DateTimeField(blank=True, null=True),
            ),
            migrations.AddField(
                model_name='invoice',
                name='anulada_por',
                field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoices_anuladas', to=settings.AUTH_USER_MODEL),
            ),
            migrations.AddField(
                model_name='invoice',
                name='motivo_anulacion',
                field=models.TextField(blank=True),
            ),
            migrations.AddField(
                model_name='notaajuste',
                name='anulada_por',
                field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notas_ajuste_anuladas', to=settings.AUTH_USER_MODEL),
            ),
            migrations.AddField(
                model_name='notaajuste',
                name='motivo_anulacion',
                field=models.TextField(blank=True),
            ),
        ]),
        separate_create_model(FacturacionRegistroAnulacion, CREATE_REGISTRO_ANULACION),
    ]
