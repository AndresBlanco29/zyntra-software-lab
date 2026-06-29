from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _to_decimal(value, default='0'):
	try:
		return Decimal(str(value if value is not None else default))
	except (InvalidOperation, TypeError, ValueError):
		return Decimal(default)


def resolve_line_case_weight(*, case_weight=None, presentacion=None):
	if case_weight is not None:
		return _to_decimal(case_weight, '0')
	if presentacion is not None and getattr(presentacion, 'peso_por_caja', None) is not None:
		return _to_decimal(presentacion.peso_por_caja, '0')
	return Decimal('0')


def build_shipment_summary_from_lines(lines):
	total_cases = 0
	total_weight = Decimal('0')
	for line in lines or []:
		quantity = int(line.get('quantity') or 0)
		total_cases += quantity
		case_weight = resolve_line_case_weight(
			case_weight=line.get('case_weight'),
			presentacion=line.get('presentacion'),
		)
		if case_weight > 0 and quantity > 0:
			total_weight += case_weight * Decimal(str(quantity))
	return {
		'total_cases': total_cases,
		'total_weight': total_weight.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP),
	}


def _line_from_model_item(item, *, quantity_attr='cantidad'):
	return {
		'quantity': getattr(item, quantity_attr, 0),
		'case_weight': getattr(item, 'peso_por_caja', None),
		'presentacion': getattr(item, 'presentacion', None),
	}


def build_shipment_summary_from_pedido_items(items, *, quantity_attr='cantidad'):
	return build_shipment_summary_from_lines([
		_line_from_model_item(item, quantity_attr=quantity_attr)
		for item in items or []
	])


def build_shipment_summary_from_invoice(invoice):
	items = invoice.items.all() if hasattr(invoice, 'items') else invoice
	return build_shipment_summary_from_lines([
		{
			'quantity': item.cantidad_facturada,
			'case_weight': item.peso_por_caja,
			'presentacion': getattr(item, 'presentacion', None),
		}
		for item in items
	])


def build_shipment_summary_from_cart_entries(entries):
	lines = []
	for entry in entries or []:
		if isinstance(entry, dict):
			lines.append({
				'quantity': entry.get('cantidad'),
				'presentacion': entry.get('presentacion'),
			})
	return build_shipment_summary_from_lines(lines)


def build_shipment_summary_from_vendor_cart_products(productos):
	lines = []
	for producto in productos or []:
		presentacion = None
		presentacion_id = producto.get('presentacion_id')
		for option in producto.get('presentaciones') or []:
			if str(option.id) == str(presentacion_id):
				presentacion = option
				break
		lines.append({
			'quantity': producto.get('cantidad'),
			'presentacion': presentacion,
		})
	return build_shipment_summary_from_lines(lines)


def unwrap_quote_rows(rows):
	items = []
	for row in rows or []:
		if isinstance(row, dict) and row.get('item') is not None:
			items.append(row['item'])
		elif hasattr(row, 'item'):
			items.append(row.item)
		else:
			items.append(row)
	return items
