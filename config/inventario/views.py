from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from config.productos.models import Presentacion
from config.usuarios.permissions import internal_permission_required

from .models import InventarioMovimiento, StockPresentacion
from .services import registrar_ajuste_manual, registrar_entrada_manual, registrar_salida_manual


def _ensure_stock_record(presentacion):
	stock, _created = StockPresentacion.objects.get_or_create(
		presentacion=presentacion,
		defaults={
			'stock_fisico': 0,
			'stock_reservado': 0,
			'stock_disponible': 0,
		},
	)
	return stock


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_inventory_list(request):
	search_term = (request.GET.get('q') or '').strip()
	presentaciones = Presentacion.objects.select_related('producto', 'producto__categoria', 'stock_operativo').filter(producto__activo=True).order_by('producto__nombre', 'nombre')

	if search_term:
		presentaciones = presentaciones.filter(
			Q(producto__nombre__icontains=search_term)
			| Q(producto__nombre_en__icontains=search_term)
			| Q(nombre__icontains=search_term)
			| Q(nombre_en__icontains=search_term)
		)

	rows = []
	total_fisico = 0
	total_reservado = 0
	total_disponible = 0
	zero_stock_count = 0

	for presentacion in presentaciones:
		stock = getattr(presentacion, 'stock_operativo', None)
		stock_fisico = stock.stock_fisico if stock else 0
		stock_reservado = stock.stock_reservado if stock else 0
		stock_disponible = stock.stock_disponible if stock else 0
		if stock_disponible <= 0:
			zero_stock_count += 1
		rows.append({
			'presentacion': presentacion,
			'stock_fisico': stock_fisico,
			'stock_reservado': stock_reservado,
			'stock_disponible': stock_disponible,
		})
		total_fisico += stock_fisico
		total_reservado += stock_reservado
		total_disponible += stock_disponible

	context = {
		'rows': rows,
		'search_term': search_term,
		'total_fisico': total_fisico,
		'total_reservado': total_reservado,
		'total_disponible': total_disponible,
		'zero_stock_count': zero_stock_count,
		'product_count': len(rows),
	}
	return render(request, 'backoffice/inventory_list.html', context)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_inventory_detail(request, presentacion_id):
	presentacion = get_object_or_404(Presentacion.objects.select_related('producto', 'producto__categoria'), id=presentacion_id)
	stock = _ensure_stock_record(presentacion)

	if request.method == 'POST':
		if not request.user.has_internal_permission('backoffice.orders.manage'):
			messages.error(request, _('You do not have permission to modify inventory.'))
			return redirect('backoffice_inventory_detail', presentacion_id=presentacion.id)

		action = (request.POST.get('action') or '').strip()
		observation = (request.POST.get('observacion') or '').strip()

		try:
			if action == 'entrada':
				quantity = max(int(request.POST.get('cantidad') or 0), 1)
				registrar_entrada_manual(presentacion=presentacion, cantidad=quantity, observacion=observation, creado_por=request.user)
				messages.success(request, _('Inventory entry recorded successfully.'))
			elif action == 'salida':
				quantity = max(int(request.POST.get('cantidad') or 0), 1)
				registrar_salida_manual(presentacion=presentacion, cantidad=quantity, observacion=observation, creado_por=request.user)
				messages.success(request, _('Inventory exit recorded successfully.'))
			elif action == 'ajuste':
				delta_quantity = int(request.POST.get('delta_cantidad') or 0)
				registrar_ajuste_manual(presentacion=presentacion, delta_cantidad=delta_quantity, observacion=observation, creado_por=request.user)
				messages.success(request, _('Inventory adjustment recorded successfully.'))
			else:
				raise ValidationError(_('Select a valid inventory action.'))
		except (ValidationError, ValueError) as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('backoffice_inventory_detail', presentacion_id=presentacion.id)

	movements = InventarioMovimiento.objects.select_related('creado_por').filter(presentacion=presentacion).order_by('-creado_en', '-id')[:50]
	stock.refresh_from_db()
	context = {
		'presentacion': presentacion,
		'stock': stock,
		'movements': movements,
	}
	return render(request, 'backoffice/inventory_detail.html', context)
