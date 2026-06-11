from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from config.productos.models import Presentacion
from config.usuarios.permissions import internal_permission_required

from .models import InventarioMovimiento, StockPresentacion, StockProductoFraccionado
from .services import (
	_resolve_fractional_rollup_presentacion,
	registrar_ajuste_manual,
	registrar_entrada_manual,
	registrar_salida_manual,
)


def _parse_required_positive_integer(raw_value, error_message):
	try:
		value = int(raw_value)
	except (TypeError, ValueError):
		raise ValidationError(error_message)
	if value <= 0:
		raise ValidationError(error_message)
	return value


def _parse_required_non_zero_integer(raw_value, error_message):
	try:
		value = int(raw_value)
	except (TypeError, ValueError):
		raise ValidationError(error_message)
	if value == 0:
		raise ValidationError(error_message)
	return value


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


def _build_fractional_stock_summary(stock):
	rollup_presentacion = _resolve_fractional_rollup_presentacion(stock.producto_id, stock.contenido)
	if rollup_presentacion is None:
		return {
			'producto': stock.producto,
			'presentacion': None,
			'contenido': stock.contenido,
			'stock_fisico': stock.stock_fisico,
			'unidades_por_presentacion': None,
		}
	return {
		'producto': stock.producto,
		'presentacion': rollup_presentacion,
		'contenido': stock.contenido,
		'stock_fisico': stock.stock_fisico,
		'unidades_por_presentacion': max(int(getattr(rollup_presentacion, 'unidades', 0) or 0), 1),
	}


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_inventory_list(request):
	search_term = (request.GET.get('q') or '').strip()
	presentaciones = Presentacion.objects.select_related('producto', 'producto__categoria', 'stock_operativo').filter(producto__activo=True).order_by('producto__nombre', 'nombre')
	fractional_stocks = StockProductoFraccionado.objects.select_related('producto').order_by('producto__nombre', 'contenido')

	if search_term:
		presentaciones = presentaciones.filter(
			Q(producto__nombre__icontains=search_term)
			| Q(producto__nombre_en__icontains=search_term)
			| Q(nombre__icontains=search_term)
			| Q(nombre_en__icontains=search_term)
		)
		fractional_stocks = fractional_stocks.filter(
			Q(producto__nombre__icontains=search_term)
			| Q(producto__nombre_en__icontains=search_term)
			| Q(contenido__icontains=search_term)
		)

	rows = []
	total_fisico = 0
	total_reservado = 0
	total_disponible = 0
	zero_stock_count = 0
	total_fractional_stock = 0
	fractional_rows = []
	fractional_by_presentacion_id = {}

	for stock in fractional_stocks:
		if stock.stock_fisico <= 0:
			continue
		summary = _build_fractional_stock_summary(stock)
		fractional_rows.append(summary)
		total_fractional_stock += stock.stock_fisico
		if summary['presentacion'] is not None:
			fractional_by_presentacion_id[summary['presentacion'].id] = summary

	for presentacion in presentaciones:
		stock = getattr(presentacion, 'stock_operativo', None)
		stock_fisico = stock.stock_fisico if stock else 0
		stock_reservado = stock.stock_reservado if stock else 0
		stock_disponible = stock.stock_disponible if stock else 0
		if stock_disponible <= 0:
			zero_stock_count += 1
		fractional_match = fractional_by_presentacion_id.get(presentacion.id)
		rows.append({
			'presentacion': presentacion,
			'stock_fisico': stock_fisico,
			'stock_reservado': stock_reservado,
			'stock_disponible': stock_disponible,
			'fractional_stock': fractional_match['stock_fisico'] if fractional_match else 0,
			'fractional_content_label': fractional_match['contenido'] if fractional_match else '',
			'physical_summary': (
				f"{stock_fisico} {presentacion.nombre_traducido} + {fractional_match['stock_fisico']} {fractional_match['contenido']}".strip()
				if fractional_match and fractional_match['stock_fisico'] > 0 else f"{stock_fisico} {presentacion.nombre_traducido}"
			),
		})
		total_fisico += stock_fisico
		total_reservado += stock_reservado
		total_disponible += stock_disponible

	context = {
		'rows': rows,
		'fractional_rows': fractional_rows,
		'search_term': search_term,
		'total_fisico': total_fisico,
		'total_reservado': total_reservado,
		'total_disponible': total_disponible,
		'total_fractional_stock': total_fractional_stock,
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
			if not observation:
				raise ValidationError(_('Observation is required for all inventory movements.'))
			if action == 'entrada':
				quantity = _parse_required_positive_integer(
					request.POST.get('cantidad'),
					_('Enter a quantity greater than zero for manual entries and exits.'),
				)
				registrar_entrada_manual(presentacion=presentacion, cantidad=quantity, observacion=observation, creado_por=request.user)
				messages.success(request, _('Inventory entry recorded successfully.'))
			elif action == 'salida':
				quantity = _parse_required_positive_integer(
					request.POST.get('cantidad'),
					_('Enter a quantity greater than zero for manual entries and exits.'),
				)
				registrar_salida_manual(presentacion=presentacion, cantidad=quantity, observacion=observation, creado_por=request.user)
				messages.success(request, _('Inventory exit recorded successfully.'))
			elif action == 'ajuste':
				delta_quantity = _parse_required_non_zero_integer(
					request.POST.get('delta_cantidad'),
					_('Enter a positive or negative adjustment delta different from zero.'),
				)
				registrar_ajuste_manual(presentacion=presentacion, delta_cantidad=delta_quantity, observacion=observation, creado_por=request.user)
				messages.success(request, _('Inventory adjustment recorded successfully.'))
			else:
				raise ValidationError(_('Select a valid inventory action.'))
		except (ValidationError, ValueError) as exc:
			messages.error(request, exc.messages[0] if getattr(exc, 'messages', None) else str(exc))
		return redirect('backoffice_inventory_detail', presentacion_id=presentacion.id)

	movements = InventarioMovimiento.objects.select_related('creado_por').filter(presentacion=presentacion).order_by('-creado_en', '-id')[:50]
	consolidation_movements = InventarioMovimiento.objects.select_related('creado_por').filter(
		presentacion=presentacion,
		tipo__in=['CONSOLIDACION_FRACCIONADA', 'DESCONSOLIDACION_FRACCIONADA'],
	).order_by('-creado_en', '-id')[:50]
	fractional_stocks = [
		_build_fractional_stock_summary(stock)
		for stock in StockProductoFraccionado.objects.filter(producto=presentacion.producto).order_by('contenido')
		if stock.stock_fisico > 0
	]
	stock.refresh_from_db()
	context = {
		'presentacion': presentacion,
		'stock': stock,
		'movements': movements,
		'consolidation_movements': consolidation_movements,
		'fractional_stocks': fractional_stocks,
	}
	return render(request, 'backoffice/inventory_detail.html', context)
