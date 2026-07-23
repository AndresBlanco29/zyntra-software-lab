from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0027_landed_cost_and_free_gift'),
    ]

    operations = [
        migrations.AddField(
            model_name='promocion',
            name='imagen',
            field=models.ImageField(
                blank=True,
                help_text='Optional representative image shown on combo cards in the catalog.',
                null=True,
                upload_to='promociones/',
                verbose_name='Combo image',
            ),
        ),
    ]
