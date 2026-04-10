from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0004_alter_usuario_role'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usuario',
            name='role',
            field=models.CharField(choices=[('admin', 'Administrador'), ('vendedor', 'Vendedor'), ('backoffice', 'BackOffice'), ('cliente', 'Cliente')], default='cliente', max_length=20),
        ),
    ]