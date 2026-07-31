from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai_assistant', '0006_alter_assistantdomainevent_event_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='assistantconfiguration',
            name='delivery_coverage',
            field=models.CharField(default='Georgia, Alabama y Tennessee', max_length=250),
        ),
        migrations.AddField(
            model_name='assistantconfiguration',
            name='location_address',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='assistantconfiguration',
            name='location_map_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='assistantconfiguration',
            name='support_email',
            field=models.EmailField(default='lamtortillagrocery@gmail.com', max_length=254),
        ),
        migrations.AddField(
            model_name='assistantconfiguration',
            name='support_phone',
            field=models.CharField(default='+1 (470) 967-2782', max_length=40),
        ),
        migrations.AddField(
            model_name='assistantconfiguration',
            name='support_whatsapp',
            field=models.CharField(default='17866516897', max_length=40),
        ),
    ]
