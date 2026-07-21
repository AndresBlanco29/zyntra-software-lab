from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0015_tipocliente'),
        ('productos', '0021_presentacion_pallet_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='promocion',
            name='tipos_cliente',
            field=models.ManyToManyField(
                blank=True,
                help_text='Leave empty to apply to every customer type.',
                related_name='promociones',
                to='clientes.tipocliente',
                verbose_name='Customer types',
            ),
        ),
        migrations.AlterField(
            model_name='promocion',
            name='cantidad_minima',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='Minimum quantity'),
        ),
        migrations.AlterField(
            model_name='promocion',
            name='tipo_beneficio',
            field=models.CharField(
                blank=True,
                choices=[
                    ('PERCENT', 'Percentage'),
                    ('FIXED', 'Fixed dollars per unit'),
                    ('FREE_UNITS', 'Free units'),
                    ('PRECIO_ESPECIAL', 'Special unit price'),
                ],
                max_length=20,
                verbose_name='Benefit type',
            ),
        ),
        migrations.AlterField(
            model_name='promocion',
            name='valor_beneficio',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='Benefit value'),
        ),
        migrations.CreateModel(
            name='PromocionEscala',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cantidad_minima', models.PositiveIntegerField(default=1, verbose_name='Minimum quantity')),
                (
                    'tipo_beneficio',
                    models.CharField(
                        choices=[
                            ('PERCENT', 'Percentage'),
                            ('FIXED', 'Fixed dollars per unit'),
                            ('FREE_UNITS', 'Free units'),
                            ('PRECIO_ESPECIAL', 'Special unit price'),
                        ],
                        default='PERCENT',
                        max_length=20,
                        verbose_name='Benefit type',
                    ),
                ),
                (
                    'valor_beneficio',
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text=(
                            'Percentage (e.g. 15), dollars per unit (e.g. 2.00), or special unit price, '
                            'depending on the benefit type. Not used for Free units.'
                        ),
                        max_digits=10,
                        null=True,
                        verbose_name='Benefit value',
                    ),
                ),
                (
                    'unidades_gratis',
                    models.PositiveIntegerField(
                        blank=True,
                        help_text='Only used when the benefit type is "Free units", e.g. buy 10 -> 1 free.',
                        null=True,
                        verbose_name='Free units',
                    ),
                ),
                ('orden', models.PositiveSmallIntegerField(default=0, verbose_name='Display order')),
                (
                    'promocion',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='escalas',
                        to='productos.promocion',
                        verbose_name='Promotion',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Promotion scale',
                'verbose_name_plural': 'Promotion scales',
                'ordering': ['cantidad_minima'],
            },
        ),
        migrations.AddConstraint(
            model_name='promocionescala',
            constraint=models.UniqueConstraint(fields=('promocion', 'cantidad_minima'), name='uniq_promocion_escala_cantidad'),
        ),
    ]
