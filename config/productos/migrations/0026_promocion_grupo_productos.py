from django.db import migrations, models
import django.db.models.deletion


def backfill_promocion_producto_rows(apps, schema_editor):
    Promocion = apps.get_model('productos', 'Promocion')
    PromocionProducto = apps.get_model('productos', 'PromocionProducto')

    rows = []
    for promocion in Promocion.objects.exclude(producto_id__isnull=True).iterator():
        rows.append(
            PromocionProducto(
                promocion_id=promocion.id,
                producto_id=promocion.producto_id,
                presentacion_id=promocion.presentacion_id,
            )
        )
    if rows:
        PromocionProducto.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0025_drop_promocion_legacy_columns_if_present'),
    ]

    operations = [
        migrations.AddField(
            model_name='promocion',
            name='alcance',
            field=models.CharField(
                choices=[('INDIVIDUAL', 'Single product'), ('GRUPO', 'Product combo (sum quantities)')],
                default='INDIVIDUAL',
                max_length=20,
                verbose_name='Scope',
            ),
        ),
        migrations.AlterField(
            model_name='promocion',
            name='producto',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='promociones',
                to='productos.producto',
                verbose_name='Product',
            ),
        ),
        migrations.CreateModel(
            name='PromocionProducto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('presentacion', models.ForeignKey(
                    blank=True,
                    help_text='Leave empty to include every presentation of this product.',
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='promociones_grupo',
                    to='productos.presentacion',
                    verbose_name='Presentation',
                )),
                ('producto', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='promociones_grupo',
                    to='productos.producto',
                    verbose_name='Product',
                )),
                ('promocion', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='productos_grupo',
                    to='productos.promocion',
                    verbose_name='Promotion',
                )),
            ],
            options={
                'verbose_name': 'Promotion product',
                'verbose_name_plural': 'Promotion products',
                'ordering': ['id'],
            },
        ),
        migrations.AddConstraint(
            model_name='promocionproducto',
            constraint=models.UniqueConstraint(
                fields=('promocion', 'producto', 'presentacion'),
                name='uniq_promocion_grupo_producto_presentacion',
            ),
        ),
        migrations.RunPython(backfill_promocion_producto_rows, migrations.RunPython.noop),
    ]
