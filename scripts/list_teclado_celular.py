import os, sys, django
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')
django.setup()
from config.productos.models import Producto, Presentacion
from django.db.models import Q

for term in ('teclado','celular'):
    print('\n---', term.upper(), '---')
    prods = Producto.objects.filter(nombre__icontains=term)
    print('Productos matching:', prods.count())
    for p in prods:
        print('P:', p.id, p.nombre, 'codigo_barras=', p.codigo_barras, 'quickbooks_id=', p.quickbooks_id)
    pres = Presentacion.objects.select_related('producto').filter(Q(nombre__icontains=term) | Q(producto__nombre__icontains=term))
    print('Presentaciones matching:', pres.count())
    for pr in pres:
        print('PR:', pr.id, 'producto=', pr.producto.nombre, 'presentacion=', pr.nombre, 'quickbooks_id=', pr.quickbooks_id)
