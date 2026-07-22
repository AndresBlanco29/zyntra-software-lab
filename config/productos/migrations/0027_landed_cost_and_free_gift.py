from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0026_promocion_grupo_productos'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfiguracionLandedCost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('PERCENT', 'Percent of RCost'), ('FIXED', 'Fixed dollars per unit')], default='PERCENT', max_length=20)),
                ('valor', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
            ],
            options={
                'verbose_name': 'Landed Cost configuration',
                'verbose_name_plural': 'Landed Cost configuration',
            },
        ),
        migrations.AddField(
            model_name='presentacion',
            name='landed_cost_override_tipo',
            field=models.CharField(
                blank=True,
                choices=[('', 'Use global Landed Cost'), ('PERCENT', 'Percent override'), ('FIXED', 'Fixed $ override')],
                default='',
                max_length=20,
                verbose_name='Landed Cost override type',
            ),
        ),
        migrations.AddField(
            model_name='presentacion',
            name='landed_cost_override_valor',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Percent or fixed dollars depending on the override type. Leave empty to use the global Landed Cost.',
                max_digits=10,
                null=True,
                verbose_name='Landed Cost override value',
            ),
        ),
        migrations.AlterField(
            model_name='presentacion',
            name='costo',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Real cost from QuickBooks / catalog cost used as the base RCost.',
                max_digits=10,
                null=True,
                verbose_name='RCost',
            ),
        ),
        migrations.AddField(
            model_name='promocionescala',
            name='presentacion_regalo',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional. When set, Free units grants this presentation as a FREE line (e.g. buy 120 of product A, receive 1 free case of product B). When empty, Free units stays as an equivalent discount on the same product.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='promociones_escala_regalo',
                to='productos.presentacion',
                verbose_name='Free product presentation',
            ),
        ),
    ]
