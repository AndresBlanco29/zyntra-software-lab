from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai_assistant', '0010_assistantcustomersuccessprofile'),
    ]

    operations = [
        migrations.AddField(
            model_name='assistantconversation',
            name='shopping_context',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
