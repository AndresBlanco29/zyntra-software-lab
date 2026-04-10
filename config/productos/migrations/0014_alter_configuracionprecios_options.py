from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0013_configuracionprecios'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='configuracionprecios',
            options={
                'verbose_name': 'Price configuration',
                'verbose_name_plural': 'Price configuration',
            },
        ),
    ]