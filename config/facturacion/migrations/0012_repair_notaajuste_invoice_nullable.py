from copy import copy

from django.db import migrations


def repair_notaajuste_invoice_nullable(apps, schema_editor):
    NotaAjuste = apps.get_model('facturacion', 'NotaAjuste')
    model = NotaAjuste
    new_field = model._meta.get_field('invoice')
    old_field = copy(new_field)
    old_field.null = False
    old_field.blank = False

    schema_editor.alter_field(model, old_field, new_field, strict=False)


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('facturacion', '0011_notaajusteitem_contenido_fraccionado'),
    ]

    operations = [
        migrations.RunPython(repair_notaajuste_invoice_nullable, migrations.RunPython.noop),
    ]
