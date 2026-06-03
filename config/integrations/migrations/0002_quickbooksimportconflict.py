from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='QuickBooksImportConflict',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entity_type', models.CharField(choices=[('CUSTOMER', 'Customer'), ('ITEM', 'Item'), ('INVOICE', 'Invoice'), ('CREDIT_MEMO', 'Credit memo')], max_length=30)),
                ('quickbooks_id', models.CharField(max_length=100)),
                ('doc_number', models.CharField(blank=True, max_length=100)),
                ('display_name', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(choices=[('CONFLICT', 'Conflict'), ('MATCHED', 'Matched')], default='CONFLICT', max_length=20)),
                ('reason', models.TextField(blank=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('local_model', models.CharField(blank=True, max_length=50)),
                ('local_record_id', models.PositiveIntegerField(blank=True, null=True)),
                ('first_seen_at', models.DateTimeField(auto_now_add=True)),
                ('last_seen_at', models.DateTimeField(auto_now=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ('-last_seen_at',),
            },
        ),
        migrations.AddConstraint(
            model_name='quickbooksimportconflict',
            constraint=models.UniqueConstraint(fields=('entity_type', 'quickbooks_id'), name='quickbooks_import_conflict_unique_record'),
        ),
    ]