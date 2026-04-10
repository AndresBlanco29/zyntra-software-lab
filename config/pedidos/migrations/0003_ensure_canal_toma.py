from django.db import migrations


def ensure_canal_toma(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SHOW COLUMNS FROM pedidos_pedidocompra LIKE %s', ['canal_toma'])
        exists = cursor.fetchone()
        if not exists:
            cursor.execute(
                "ALTER TABLE pedidos_pedidocompra ADD COLUMN canal_toma varchar(20) NOT NULL DEFAULT ''"
            )


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0002_align_historical_table'),
    ]

    operations = [
        migrations.RunPython(ensure_canal_toma, reverse_code=noop_reverse),
    ]