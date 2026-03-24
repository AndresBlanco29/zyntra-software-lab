import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from config.productos.models import Categoria, Marca

# Obtener las categorías
bebidas = Categoria.objects.get(nombre='bebidas')
fritolay = Categoria.objects.get(nombre='fritolay')

# Obtener las marcas
coca_cola = Marca.objects.get(nombre='Coca-Cola')
marca_papa = Marca.objects.get(nombre='marca papa')
maseca = Marca.objects.get(nombre='maseca')
doritos = Marca.objects.get(nombre='Doritos')
h20 = Marca.objects.get(nombre='h20')

# Asignar categorías a marcas
coca_cola.categorias.add(bebidas)
marca_papa.categorias.add(fritolay)
maseca.categorias.add(fritolay)
doritos.categorias.add(fritolay)

print("✓ Bebidas:")
print(f"  - {coca_cola.nombre}")
print(f"  - {h20.nombre}")
print("\n✓ Fritolay:")
print(f"  - {doritos.nombre}")
print(f"  - {maseca.nombre}")
print(f"  - {marca_papa.nombre}")
