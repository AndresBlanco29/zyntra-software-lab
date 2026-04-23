from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0004_cliente_review_workflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='nivel_precio',
            field=models.PositiveSmallIntegerField(choices=[(1, 'Precio 1'), (2, 'Precio 2'), (3, 'Precio 3'), (4, 'Precio 4'), (5, 'Precio 5')], default=1),
        ),
    ]