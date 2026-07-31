import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai_assistant', '0007_assistantconfiguration_commercial_contact'),
        ('productos', '0029_configuraciondescuentosporcentaje'),
    ]

    operations = [
        migrations.CreateModel(
            name='AssistantProductAlias',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('alias', models.CharField(max_length=160, unique=True)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('brand', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='assistant_aliases', to='productos.marca')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='assistant_aliases', to='productos.producto')),
            ],
            options={
                'verbose_name': 'Assistant product alias',
                'verbose_name_plural': 'Assistant product aliases',
            },
        ),
    ]
