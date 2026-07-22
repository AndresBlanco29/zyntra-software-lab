from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0015_tipocliente'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='web_access_password',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Plaintext copy of the customer web password for authorized staff display.',
                max_length=128,
            ),
        ),
    ]
