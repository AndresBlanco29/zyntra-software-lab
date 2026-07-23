from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0011_repair_stock_disponible_from_physical'),
    ]

    operations = [
        migrations.AlterField(
            model_name='inventariomovimiento',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('ENTRADA_MANUAL', 'Manual entry'),
                    ('SALIDA_MANUAL', 'Manual exit'),
                    ('AJUSTE_POSITIVO', 'Positive adjustment'),
                    ('AJUSTE_NEGATIVO', 'Negative adjustment'),
                    ('CONSOLIDACION_FRACCIONADA', 'Fractional stock consolidation'),
                    ('DESCONSOLIDACION_FRACCIONADA', 'Fractional stock deconsolidation'),
                    ('RESERVA_PEDIDO', 'Order reservation'),
                    ('LIBERACION_PEDIDO', 'Order reservation release'),
                    ('SALIDA_PICKING', 'Picking deduction'),
                    ('SALIDA_REGALO', 'Free promotional product deduction'),
                    ('AJUSTE_PICKING', 'Picking adjustment'),
                    ('ENTRADA_NOTA_CREDITO', 'Credit note return'),
                    ('REVERSO_NOTA_CREDITO', 'Credit note reversal'),
                    ('ANULACION_PEDIDO', 'Order cancellation reversal'),
                ],
                max_length=30,
            ),
        ),
    ]
