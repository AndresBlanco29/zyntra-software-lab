from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0015_notaajusteaplicacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='quickbooks_id',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='sync_status',
            field=models.CharField(choices=[('PENDING', 'Pending'), ('SYNCED', 'Synced'), ('FAILED', 'Failed')], db_index=True, default='PENDING', max_length=20),
        ),
        migrations.AddField(
            model_name='notaajuste',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='notaajuste',
            name='quickbooks_id',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='notaajuste',
            name='sync_status',
            field=models.CharField(choices=[('PENDING', 'Pending'), ('SYNCED', 'Synced'), ('FAILED', 'Failed')], db_index=True, default='PENDING', max_length=20),
        ),
    ]