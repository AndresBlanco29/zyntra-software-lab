import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('facturacion', '0029_invoiceitem_es_regalo'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='editada_en',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='editada_por',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='invoices_editadas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='invoice',
            name='veces_editada',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='InvoiceEditHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('motivo', models.TextField()),
                ('snapshot_antes', models.JSONField(blank=True, default=dict)),
                ('snapshot_despues', models.JSONField(blank=True, default=dict)),
                ('editado_en', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('editado_por', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='invoice_edit_history',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('invoice', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='edit_history',
                    to='facturacion.invoice',
                )),
            ],
            options={
                'verbose_name': 'Invoice edit history',
                'verbose_name_plural': 'Invoice edit history',
                'ordering': ('-editado_en', '-id'),
            },
        ),
    ]
