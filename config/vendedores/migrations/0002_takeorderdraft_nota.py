# Generated manually for draft order comment persistence

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vendedores', '0001_take_order_draft'),
    ]

    operations = [
        migrations.AddField(
            model_name='takeorderdraft',
            name='nota',
            field=models.TextField(blank=True, default=''),
        ),
    ]
