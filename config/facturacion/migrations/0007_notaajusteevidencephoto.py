from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		('facturacion', '0006_delivery_estimated_delivery_at'),
	]

	operations = [
		migrations.CreateModel(
			name='NotaAjusteEvidencePhoto',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('image', models.ImageField(upload_to='invoice-notes/evidence/')),
				('uploaded_at', models.DateTimeField(auto_now_add=True)),
				('nota', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='evidence_photos', to='facturacion.notaajuste')),
			],
			options={
				'ordering': ('uploaded_at', 'id'),
			},
		),
	]