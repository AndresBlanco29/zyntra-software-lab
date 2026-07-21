from decimal import Decimal

from django.db import migrations


def backfill_escalas(apps, schema_editor):
    Promocion = apps.get_model('productos', 'Promocion')
    PromocionEscala = apps.get_model('productos', 'PromocionEscala')

    rows = []
    for promocion in Promocion.objects.all().iterator():
        cantidad_minima = promocion.cantidad_minima or 1
        tipo_beneficio = promocion.tipo_beneficio or 'PERCENT'
        valor_beneficio = promocion.valor_beneficio
        if valor_beneficio is None:
            valor_beneficio = Decimal('0.00')
        rows.append(
            PromocionEscala(
                promocion_id=promocion.id,
                cantidad_minima=cantidad_minima,
                tipo_beneficio=tipo_beneficio,
                valor_beneficio=valor_beneficio,
            )
        )
    if rows:
        PromocionEscala.objects.bulk_create(rows, ignore_conflicts=True)


def restore_promocion_fields(apps, schema_editor):
    """Reverse: copy the first scale of each promotion back onto the legacy fields."""
    Promocion = apps.get_model('productos', 'Promocion')
    for promocion in Promocion.objects.all().iterator():
        escala = promocion.escalas.order_by('cantidad_minima').first()
        if not escala:
            continue
        promocion.cantidad_minima = escala.cantidad_minima
        promocion.tipo_beneficio = escala.tipo_beneficio
        promocion.valor_beneficio = escala.valor_beneficio or Decimal('0.00')
        promocion.save(update_fields=['cantidad_minima', 'tipo_beneficio', 'valor_beneficio'])


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0022_promocion_escalas'),
    ]

    operations = [
        migrations.RunPython(backfill_escalas, restore_promocion_fields),
    ]
