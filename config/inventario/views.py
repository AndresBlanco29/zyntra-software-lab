from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

from config.integrations.models import QuickBooksImportConflict
from config.integrations.quickbooks.services import get_connection_status
from config.productos.models import Presentacion
from config.usuarios.permissions import internal_permission_required

from .models import CompraProveedor, CompraProveedorLinea, InventarioMovimiento, Proveedor, StockPresentacion, StockProductoFraccionado
from .services import (
	_resolve_fractional_rollup_presentacion,
	registrar_ajuste_manual,
	registrar_entrada_manual,
	registrar_recepcion_compra_proveedor,
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


def _parse_supplier_purchase_lines(post_data):
	raw_presentacion_ids = post_data.getlist('presentacion_id')
	raw_quantities = post_data.getlist('cantidad')
	raw_unit_costs = post_data.getlist('costo_unitario')
	raw_descriptions = post_data.getlist('descripcion')

	line_specs = []
	for index, raw_presentacion_id in enumerate(raw_presentacion_ids):
		presentacion_id = str(raw_presentacion_id or '').strip()
		quantity = str(raw_quantities[index] if index < len(raw_quantities) else '').strip()
		unit_cost = str(raw_unit_costs[index] if index < len(raw_unit_costs) else '').strip()
		description = str(raw_descriptions[index] if index < len(raw_descriptions) else '').strip()
		if not any((presentacion_id, quantity, unit_cost, description)):
			continue
		if not (presentacion_id and quantity and unit_cost):
			raise ValidationError(_('Each purchase order line needs product, quantity, and unit cost.'))
		try:
			parsed_presentacion_id = int(presentacion_id)
			parsed_quantity = int(quantity)
			parsed_unit_cost = Decimal(unit_cost)
		except (TypeError, ValueError, InvalidOperation) as exc:
			raise ValidationError(_('One or more purchase order lines contain invalid values.')) from exc
		if parsed_quantity <= 0:
			raise ValidationError(_('Quantity must be greater than zero.'))
		if parsed_unit_cost < 0:
			raise ValidationError(_('Unit cost cannot be negative.'))
		line_specs.append({
			'presentacion_id': parsed_presentacion_id,
			'cantidad': parsed_quantity,
			'costo_unitario': parsed_unit_cost,
			'descripcion': description,
		})

	if not line_specs:
		raise ValidationError(_('Add at least one purchase order line before saving the draft.'))

	presentaciones = {
		presentacion.id: presentacion
		for presentacion in Presentacion.objects.select_related('producto').filter(
			id__in=[spec['presentacion_id'] for spec in line_specs]
		)
	}
	if len(presentaciones) != len({spec['presentacion_id'] for spec in line_specs}):
		raise ValidationError(_('One or more selected presentations no longer exist.'))

	for spec in line_specs:
		spec['presentacion'] = presentaciones[spec['presentacion_id']]
	return line_specs


def _build_purchase_order_context(*, request):
	purchases = CompraProveedor.objects.select_related('creado_por', 'inventory_received_by').prefetch_related('lineas__presentacion__producto').order_by('-creado_en', '-id')
	imported_bills = purchases.filter(
		creado_por__isnull=True,
		estado=CompraProveedor.STATUS_RECEIVED,
	).exclude(quickbooks_id__isnull=True).exclude(quickbooks_id='')
	bill_conflicts = QuickBooksImportConflict.objects.filter(
		entity_type=QuickBooksImportConflict.ENTITY_BILL,
		status=QuickBooksImportConflict.STATUS_CONFLICT,
	)
	return {
		'purchases': purchases[:25],
		'purchase_orders': purchases.exclude(id__in=imported_bills.values('id'))[:25],
		'imported_bills': imported_bills[:25],
		'purchase_presentations': Presentacion.objects.select_related('producto').order_by('producto__nombre', 'nombre'),
		'purchase_suppliers': Proveedor.objects.filter(activo=True).order_by('nombre', 'id'),
		'purchase_slots': range(4),
		'purchase_counts': {
			'draft': purchases.filter(estado=CompraProveedor.STATUS_DRAFT).count(),
			'sent': purchases.filter(estado=CompraProveedor.STATUS_SENT).count(),
			'received': purchases.filter(estado=CompraProveedor.STATUS_RECEIVED).count(),
			'imported_bills': imported_bills.count(),
			'pending_sync': purchases.filter(quickbooks_id__isnull=True).exclude(estado=CompraProveedor.STATUS_CANCELLED).count(),
		},
		'bill_conflicts_count': bill_conflicts.count(),
	}


def _build_supplier_context(*, request):
	search_term = (request.GET.get('q') or '').strip()
	status_filter = str(request.GET.get('status') or '').strip().lower()
	sync_filter = str(request.GET.get('sync') or '').strip().lower()
	suppliers = Proveedor.objects.order_by('nombre', 'id')
	if search_term:
		suppliers = suppliers.filter(
			Q(nombre__icontains=search_term)
			| Q(company_name__icontains=search_term)
			| Q(email__icontains=search_term)
			| Q(telefono__icontains=search_term)
		)
	if status_filter == 'active':
		suppliers = suppliers.filter(activo=True)
	elif status_filter == 'inactive':
		suppliers = suppliers.filter(activo=False)
	if sync_filter == 'linked':
		suppliers = suppliers.filter(quickbooks_id__isnull=False).exclude(quickbooks_id='')
	elif sync_filter == 'local':
		suppliers = suppliers.filter(Q(quickbooks_id__isnull=True) | Q(quickbooks_id=''))
	all_suppliers = Proveedor.objects.all()
	return {
		'suppliers': suppliers[:50],
		'supplier_search_term': search_term,
		'supplier_status_filter': status_filter,
		'supplier_sync_filter': sync_filter,
		'supplier_counts': {
			'total': all_suppliers.count(),
			'linked': all_suppliers.filter(quickbooks_id__isnull=False).exclude(quickbooks_id='').count(),
			'active': all_suppliers.filter(activo=True).count(),
			'with_email': all_suppliers.exclude(email='').count(),
		},
		'quickbooks_status': get_connection_status(),
	}


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_supplier_purchase_list(request):
	if request.method == 'POST':
		if not request.user.has_internal_permission('backoffice.orders.manage'):
			return HttpResponseBadRequest(_('You do not have permission to create purchase orders.'))
		try:
			supplier = None
			raw_supplier_id = str(request.POST.get('proveedor_id') or '').strip()
			if raw_supplier_id:
				try:
					supplier = Proveedor.objects.get(pk=int(raw_supplier_id))
				except (TypeError, ValueError, Proveedor.DoesNotExist) as exc:
					raise ValidationError(_('Selected supplier is invalid.')) from exc
				supplier_name = supplier.nombre
				supplier_email = supplier.email
				supplier_phone = supplier.telefono
			else:
				supplier_name = str(request.POST.get('proveedor_nombre') or '').strip()
				supplier_email = str(request.POST.get('proveedor_email') or '').strip()
				supplier_phone = str(request.POST.get('proveedor_telefono') or '').strip()
				if not supplier_name:
					raise ValidationError(_('Supplier name is required.'))
			purchase_date = parse_date(str(request.POST.get('fecha_compra') or '').strip())
			if purchase_date is None:
				raise ValidationError(_('Purchase date is required.'))
			raw_due_date = str(request.POST.get('fecha_vencimiento') or '').strip()
			due_date = parse_date(raw_due_date) if raw_due_date else None
			if raw_due_date and due_date is None:
				raise ValidationError(_('Due date is invalid.'))
			line_specs = _parse_supplier_purchase_lines(request.POST)
			with transaction.atomic():
				compra = CompraProveedor.objects.create(
					proveedor=supplier,
					proveedor_nombre=supplier_name,
					proveedor_email=supplier_email,
					proveedor_telefono=supplier_phone,
					bill_number=str(request.POST.get('bill_number') or '').strip(),
					fecha_compra=purchase_date,
					fecha_vencimiento=due_date,
					notas=str(request.POST.get('notas') or '').strip(),
					estado=CompraProveedor.STATUS_DRAFT,
					creado_por=request.user,
				)
				for spec in line_specs:
					linea = CompraProveedorLinea(
						compra=compra,
						presentacion=spec['presentacion'],
						cantidad=spec['cantidad'],
						costo_unitario=spec['costo_unitario'],
						descripcion=spec['descripcion'],
					)
					linea.full_clean()
					linea.save()
				compra.recalcular_totales(save=True)
		except ValidationError as exc:
			messages.error(request, exc.messages[0])
		else:
			messages.success(request, _('Purchase order draft created successfully.'))
			return redirect('backoffice_supplier_purchase_detail', compra_id=compra.id)

	context = _build_purchase_order_context(request=request)
	return render(request, 'backoffice/purchase_orders_list.html', context)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_supplier_purchase_detail(request, compra_id):
	compra = get_object_or_404(
		CompraProveedor.objects.select_related('creado_por', 'inventory_received_by', 'proveedor').prefetch_related('lineas__presentacion__producto'),
		pk=compra_id,
	)
	if request.method == 'POST':
		if not request.user.has_internal_permission('backoffice.orders.manage'):
			return HttpResponseBadRequest(_('You do not have permission to update purchase orders.'))
		if str(request.POST.get('action') or '').strip() == 'update_supplier_link':
			if compra.estado != CompraProveedor.STATUS_DRAFT or compra.quickbooks_id or compra.inventory_applied:
				messages.error(request, _('Only draft purchase orders can change the linked supplier.'))
				return redirect('backoffice_supplier_purchase_detail', compra_id=compra.id)
			try:
				raw_supplier_id = str(request.POST.get('proveedor_id') or '').strip()
				if not raw_supplier_id:
					raise ValidationError(_('Select a supplier from the catalog.'))
				supplier = Proveedor.objects.get(pk=int(raw_supplier_id), activo=True)
			except (TypeError, ValueError, Proveedor.DoesNotExist) as exc:
				messages.error(request, _('Selected supplier is invalid.'))
				return redirect('backoffice_supplier_purchase_detail', compra_id=compra.id)
			compra.proveedor = supplier
			compra.proveedor_nombre = supplier.nombre
			compra.proveedor_email = supplier.email
			compra.proveedor_telefono = supplier.telefono
			compra.save(update_fields=['proveedor', 'proveedor_nombre', 'proveedor_email', 'proveedor_telefono', 'actualizado_en'])
			messages.success(request, _('Linked supplier updated successfully.'))
			return redirect('backoffice_supplier_purchase_detail', compra_id=compra.id)
	return render(request, 'backoffice/purchase_order_detail.html', {'compra': compra, 'purchase_suppliers': Proveedor.objects.filter(activo=True).order_by('nombre', 'id')})


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_supplier_purchase_receive(request, compra_id):
	if request.method != 'POST':
		return HttpResponseBadRequest(_('Invalid request method.'))
	compra = get_object_or_404(CompraProveedor, pk=compra_id)
	try:
		registrar_recepcion_compra_proveedor(compra=compra, creado_por=request.user)
	except ValidationError as exc:
		messages.error(request, exc.messages[0])
	else:
		messages.success(request, _('Inventory receipt recorded successfully.'))
	return redirect('backoffice_supplier_purchase_detail', compra_id=compra_id)


@login_required
@internal_permission_required('backoffice.orders.manage')
def backoffice_supplier_purchase_cancel(request, compra_id):
	if request.method != 'POST':
		return HttpResponseBadRequest(_('Invalid request method.'))
	compra = get_object_or_404(CompraProveedor, pk=compra_id)
	if compra.accounting_locked:
		messages.error(request, _('Synced purchase orders can only be changed directly in QuickBooks.'))
		return redirect('backoffice_supplier_purchase_detail', compra_id=compra_id)
	if compra.inventory_applied:
		messages.error(request, _('Received purchase orders cannot be cancelled after inventory was loaded.'))
		return redirect('backoffice_supplier_purchase_detail', compra_id=compra_id)
	compra.estado = CompraProveedor.STATUS_CANCELLED
	compra.save(update_fields=['estado', 'actualizado_en'])
	messages.success(request, _('Purchase order cancelled successfully.'))
	return redirect('backoffice_supplier_purchase_detail', compra_id=compra_id)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_supplier_list(request):
	context = _build_supplier_context(request=request)
	return render(request, 'backoffice/suppliers_list.html', context)


@login_required
@internal_permission_required('backoffice.orders.view')
def backoffice_supplier_detail(request, supplier_id):
	supplier = get_object_or_404(Proveedor, pk=supplier_id)
	if request.method == 'POST':
		if not request.user.has_internal_permission('backoffice.orders.manage'):
			return HttpResponseBadRequest(_('You do not have permission to update suppliers.'))
		try:
			nombre = str(request.POST.get('nombre') or '').strip()
			if not nombre:
				raise ValidationError(_('Supplier name is required.'))
			supplier.nombre = nombre
			supplier.email = str(request.POST.get('email') or '').strip()
			supplier.telefono = str(request.POST.get('telefono') or '').strip()
			supplier.company_name = str(request.POST.get('company_name') or '').strip()
			supplier.notas = str(request.POST.get('notas') or '').strip()
			supplier.activo = str(request.POST.get('activo') or '').strip() == '1'
			supplier.full_clean()
			supplier.save()
		except ValidationError as exc:
			messages.error(request, exc.messages[0])
		else:
			messages.success(request, _('Supplier updated successfully.'))
			return redirect('backoffice_supplier_detail', supplier_id=supplier.id)
	return render(request, 'backoffice/supplier_detail.html', {'supplier': supplier, 'quickbooks_status': get_connection_status()})


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
