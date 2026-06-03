from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='QuickBooksConnection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('environment', models.CharField(max_length=20, unique=True)),
                ('realm_id', models.CharField(blank=True, max_length=100)),
                ('access_token', models.TextField(blank=True)),
                ('refresh_token', models.TextField(blank=True)),
                ('token_type', models.CharField(blank=True, default='Bearer', max_length=40)),
                ('scope', models.TextField(blank=True)),
                ('access_token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('refresh_token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('connected_at', models.DateTimeField(blank=True, null=True)),
                ('last_refreshed_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ('environment',)},
        ),
    ]