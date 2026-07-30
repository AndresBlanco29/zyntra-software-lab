from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('ai_assistant', '0005_assistantverificationchallenge')]

    operations = [
        migrations.AlterField(
            model_name='assistantdomainevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('REGISTRATION_SUBMITTED', 'Registration submitted'),
                    ('ACCOUNT_APPROVED', 'Account approved'),
                    ('ACCOUNT_NEEDS_CORRECTION', 'Account needs correction'),
                    ('QUOTE_READY', 'Quote ready'),
                    ('ORDER_DISPATCHED', 'Order dispatched'),
                    ('ORDER_DELIVERED', 'Order delivered'),
                ],
                max_length=40,
            ),
        ),
    ]
