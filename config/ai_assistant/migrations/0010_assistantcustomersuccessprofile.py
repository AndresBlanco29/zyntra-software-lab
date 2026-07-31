import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai_assistant', '0009_fix_assistant_support_email'),
        ('clientes', '0016_cliente_web_access_password'),
    ]

    operations = [
        migrations.CreateModel(
            name='AssistantCustomerSuccessProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('first_login_at', models.DateTimeField(blank=True, null=True)),
                ('last_login_at', models.DateTimeField(blank=True, null=True)),
                ('last_conversation_at', models.DateTimeField(blank=True, null=True)),
                ('last_module', models.CharField(blank=True, max_length=80)),
                ('last_tour', models.CharField(blank=True, max_length=80)),
                ('onboarding_learned', models.BooleanField(default=False)),
                ('first_order_at', models.DateTimeField(blank=True, null=True)),
                ('last_order_id', models.PositiveIntegerField(blank=True, null=True)),
                ('recently_viewed_products', models.JSONField(blank=True, default=list)),
                ('help_topics', models.JSONField(blank=True, default=list)),
                ('event_marks', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('cliente', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='assistant_success_profile', to='clientes.cliente')),
            ],
        ),
    ]
