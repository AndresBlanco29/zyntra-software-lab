import uuid

from django.db import migrations, models


ESTADO_CHOICES = [
    ('BORRADOR', 'Borrador'),
    ('ENVIADA', 'Enviada'),
    ('LISTA_PARA_CONFIRMACION', 'Lista para confirmacion'),
    ('CONFIRMADA_CLIENTE', 'Confirmada por cliente'),
    ('CANCELADA_CLIENTE', 'Cancelada por cliente'),
    ('APROBADA', 'Aprobada'),
    ('RECHAZADA', 'Rechazada'),
]


def _existing_columns(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(cursor, table_name)
    return {column.name for column in description}


def _add_field_if_missing(schema_editor, model, field_name, field, existing_columns):
    if field_name in existing_columns:
        return

    field.set_attributes_from_name(field_name)
    schema_editor.add_field(model, field)
    existing_columns.add(field_name)


def aplicar_campos_entrega2(apps, schema_editor):
    Cotizacion = apps.get_model('cotizaciones', 'Cotizacion')
    table_name = Cotizacion._meta.db_table
    existing_columns = _existing_columns(schema_editor, table_name)

    fields_to_add = [
        ('correo_enviado', models.BooleanField(default=False)),
        ('correo_enviado_en', models.DateTimeField(null=True, blank=True)),
        ('nota_backoffice', models.TextField(blank=True)),
        ('nota_cliente', models.TextField(blank=True)),
        ('nota_confirmacion_cliente', models.TextField(blank=True)),
        ('sms_enviado', models.BooleanField(default=False)),
        ('sms_enviado_en', models.DateTimeField(null=True, blank=True)),
        ('token_cliente', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
        ('whatsapp_enviado', models.BooleanField(default=False)),
        ('whatsapp_enviado_en', models.DateTimeField(null=True, blank=True)),
        ('whatsapp_manual_abierto', models.BooleanField(default=False)),
        ('whatsapp_manual_abierto_en', models.DateTimeField(null=True, blank=True)),
    ]

    for field_name, field in fields_to_add:
        _add_field_if_missing(schema_editor, Cotizacion, field_name, field, existing_columns)

    old_field = models.CharField(choices=ESTADO_CHOICES, default='BORRADOR', max_length=20)
    new_field = models.CharField(choices=ESTADO_CHOICES, default='BORRADOR', max_length=30)
    old_field.set_attributes_from_name('estado')
    new_field.set_attributes_from_name('estado')
    schema_editor.alter_field(Cotizacion, old_field, new_field, strict=False)


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0002_alter_cotizacion_vendedor'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(aplicar_campos_entrega2, reverse_code=noop_reverse),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='cotizacion',
                    name='correo_enviado',
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='correo_enviado_en',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='nota_backoffice',
                    field=models.TextField(blank=True),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='nota_cliente',
                    field=models.TextField(blank=True),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='nota_confirmacion_cliente',
                    field=models.TextField(blank=True),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='sms_enviado',
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='sms_enviado_en',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='token_cliente',
                    field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='whatsapp_enviado',
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='whatsapp_enviado_en',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='whatsapp_manual_abierto',
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name='cotizacion',
                    name='whatsapp_manual_abierto_en',
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name='cotizacion',
                    name='estado',
                    field=models.CharField(choices=ESTADO_CHOICES, default='BORRADOR', max_length=30),
                ),
            ],
        ),
    ]