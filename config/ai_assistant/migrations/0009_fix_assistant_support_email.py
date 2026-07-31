from django.db import migrations, models


INCORRECT_EMAIL = 'lamtortillagrocery@gmail.com'
CORRECT_EMAIL = 'latortillagrocery@gmail.com'


def fix_existing_support_email(apps, schema_editor):
    AssistantConfiguration = apps.get_model('ai_assistant', 'AssistantConfiguration')
    AssistantConfiguration.objects.filter(support_email=INCORRECT_EMAIL).update(support_email=CORRECT_EMAIL)


class Migration(migrations.Migration):
    dependencies = [
        ('ai_assistant', '0008_assistantproductalias'),
    ]

    operations = [
        migrations.AlterField(
            model_name='assistantconfiguration',
            name='support_email',
            field=models.EmailField(default=CORRECT_EMAIL, max_length=254),
        ),
        migrations.RunPython(fix_existing_support_email, migrations.RunPython.noop),
    ]
