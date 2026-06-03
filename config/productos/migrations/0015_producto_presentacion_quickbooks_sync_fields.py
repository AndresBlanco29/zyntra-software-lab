from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0014_alter_configuracionprecios_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='presentacion',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='presentacion',
            name='quickbooks_id',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='presentacion',
            name='sync_status',
            field=models.CharField(choices=[('PENDING', 'Pending'), ('SYNCED', 'Synced'), ('FAILED', 'Failed')], db_index=True, default='PENDING', max_length=20),
        ),
        migrations.AddField(
            model_name='producto',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='producto',
            name='sync_status',
            field=models.CharField(choices=[('PENDING', 'Pending'), ('SYNCED', 'Synced'), ('FAILED', 'Failed')], db_index=True, default='PENDING', max_length=20),
        ),
        migrations.AlterField(
            model_name='producto',
            name='quickbooks_id',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
    ]