from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='testimonio',
            name='comentario_en',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='testimonio',
            name='negocio_en',
            field=models.CharField(blank=True, max_length=150, null=True),
        ),
    ]