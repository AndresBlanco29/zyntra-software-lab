from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0014_deliverypayment'),
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
	]

	operations = [
		migrations.CreateModel(
			name='NotaAjusteAplicacion',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('monto', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('creada_en', models.DateTimeField(auto_now_add=True)),
				('aplicada_por', models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='aplicaciones_notas_ajuste', to=settings.AUTH_USER_MODEL)),
				('invoice', models.ForeignKey(on_delete=models.CASCADE, related_name='aplicaciones_notas_ajuste', to='facturacion.invoice')),
				('nota', models.ForeignKey(on_delete=models.CASCADE, related_name='aplicaciones', to='facturacion.notaajuste')),
			],
			options={
				'ordering': ('creada_en', 'id'),
			},
		),
	]