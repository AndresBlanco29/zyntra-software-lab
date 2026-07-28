from django.db import migrations
from django.db.models import F, Q


OPEN_WITHOUT_INVOICE = ~Q(pedido__estado__in=('INVOICE_GENERADA', 'DESPACHADO', 'CANCELADO'))


def realign_reserved_inventory(apps, schema_editor):
    """Align In Orders markers with reserve-on-pick semantics.

    - Open orders not yet verified / inventory-applied: reserved = 0
      (create no longer reserves; frees Available inflated by old create-time markers).
    - Verified / inventory-applied open orders still without invoice:
      reserved = cantidad (keep stock prepared for invoicing).
    """
    PedidoItem = apps.get_model('pedidos', 'PedidoItem')

    # Unverified open lines: drop create-time reservations.
    PedidoItem.objects.filter(
        OPEN_WITHOUT_INVOICE,
        pedido__invoice__isnull=True,
        cantidad_inventario_aplicada=0,
    ).exclude(
        pedido__estado='VERIFICADO_AJUSTADO',
    ).exclude(
        pedido__picking_verificado_en__isnull=False,
    ).update(cantidad_reservada_inventario=0)

    # Verified / applied open lines: keep reservation equal to the fulfillment wave.
    PedidoItem.objects.filter(
        OPEN_WITHOUT_INVOICE,
        pedido__invoice__isnull=True,
    ).filter(
        Q(cantidad_inventario_aplicada__gt=0)
        | Q(pedido__estado='VERIFICADO_AJUSTADO')
        | Q(pedido__picking_verificado_en__isnull=False)
    ).update(cantidad_reservada_inventario=F('cantidad'))


def noop_reverse(apps, schema_editor):
    # Data realignment is not safely reversible.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0017_pedidoitem_es_regalo'),
    ]

    operations = [
        migrations.RunPython(realign_reserved_inventory, noop_reverse),
    ]
