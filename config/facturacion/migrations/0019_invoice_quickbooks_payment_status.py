from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0018_alter_notaajuste_motivo'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='qb_due_date',
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='qb_email_status',
            field=models.CharField(blank=True, choices=[('', 'Unknown'), ('NOT_SET', 'Not set'), ('NEED_TO_SEND', 'Need to send'), ('EMAIL_SENT', 'Email sent')], default='', max_length=20),
        ),
        migrations.AddField(
            model_name='invoice',
            name='qb_payment_status',
            field=models.CharField(blank=True, choices=[('', 'Not synced'), ('OPEN', 'Open balance'), ('DUE', 'Due'), ('DUE_TODAY', 'Due today'), ('OVERDUE', 'Overdue'), ('PAID', 'Paid'), ('DEPOSITED', 'Deposited')], db_index=True, default='', max_length=20),
        ),
    ]
