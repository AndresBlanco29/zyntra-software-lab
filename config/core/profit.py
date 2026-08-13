"""Shared profit / margin calculations for orders and Reports Center."""

from decimal import Decimal, ROUND_HALF_UP

DECIMAL_ZERO = Decimal('0.00')


def _to_decimal(value, default='0'):
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(str(default))


def _quantize_money(value):
    return _to_decimal(value, '0').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def safe_profit_percentage(numerator, denominator):
    denominator_decimal = _to_decimal(denominator, '0')
    if denominator_decimal <= 0:
        return None
    return (_to_decimal(numerator, '0') / denominator_decimal * Decimal('100')).quantize(
        Decimal('0.1'),
        rounding=ROUND_HALF_UP,
    )


def calculate_profit_percentage(*, cost, sale_price):
    """Margin on selling price: (sale_price - cost) / sale_price * 100."""
    if cost is None:
        return None

    cost_decimal = _quantize_money(cost)
    sale_decimal = _quantize_money(sale_price)
    if sale_decimal <= 0:
        return None

    percentage = (Decimal('1') - (cost_decimal / sale_decimal)) * Decimal('100')
    return percentage.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def calculate_unit_profit_amount(*, cost, sale_price):
    if cost is None:
        return None

    return _quantize_money(_quantize_money(sale_price) - _quantize_money(cost))


def calculate_profit_from_revenue(*, cost_per_unit, quantity, revenue):
    """Profit for a line when revenue is already known (e.g. invoiced subtotal)."""
    qty = Decimal(str(quantity or 0))
    unit_cost = _quantize_money(cost_per_unit) if cost_per_unit is not None else DECIMAL_ZERO
    cogs = unit_cost * qty
    line_revenue = _quantize_money(revenue)
    profit_amount = line_revenue - cogs
    return {
        'cogs': cogs,
        'revenue': line_revenue,
        'profit_amount': profit_amount,
        'profit_percent': safe_profit_percentage(profit_amount, line_revenue),
        'has_cost': cost_per_unit is not None,
    }


def build_order_line_profit(
    *,
    cost,
    list_price,
    quantity,
    descuento_aplicado=False,
    descuento_monto=0,
):
    """Profit for an order/quote line using list price, discounts and catalog cost."""
    from config.pedidos.services import calcular_precio_unitario_neto_item

    net_unit_price = calcular_precio_unitario_neto_item(
        precio=list_price,
        descuento_aplicado=descuento_aplicado,
        descuento_monto=descuento_monto,
    )
    qty = int(quantity or 0)
    line_revenue = _quantize_money(net_unit_price * Decimal(str(qty)))
    unit_profit_amount = calculate_unit_profit_amount(cost=cost, sale_price=net_unit_price)
    line_profit_amount = (
        _quantize_money(unit_profit_amount * Decimal(str(qty)))
        if unit_profit_amount is not None
        else None
    )

    return {
        'unit_profit_amount': unit_profit_amount,
        'profit_percent': calculate_profit_percentage(cost=cost, sale_price=net_unit_price),
        'line_profit_amount': line_profit_amount,
        'line_profit_percent': (
            safe_profit_percentage(line_profit_amount, line_revenue)
            if line_profit_amount is not None
            else None
        ),
        'line_revenue': line_revenue,
        'net_unit_price': net_unit_price,
        'has_cost': cost is not None,
    }


def resolve_line_cost(presentacion):
    """Prefer RCost + Landed Cost when a presentation is available."""
    if presentacion is None:
        return None
    effective = getattr(presentacion, 'effective_cost', None)
    if callable(effective):
        try:
            return effective()
        except Exception:
            pass
    if effective is not None:
        return effective
    from config.productos.landed_cost import resolve_effective_cost

    return resolve_effective_cost(presentacion)


def attach_profit_to_order_item(item):
    presentacion = getattr(item, 'presentacion', None)
    profit = build_order_line_profit(
        cost=resolve_line_cost(presentacion),
        list_price=getattr(item, 'precio', 0),
        quantity=getattr(item, 'cantidad', 0),
        descuento_aplicado=bool(getattr(item, 'descuento_aplicado', False)),
        descuento_monto=getattr(item, 'descuento_monto', 0),
    )
    item.profit = profit
    return profit


def summarize_order_profit(lines):
    """Aggregate profit for iterable of rows/items with `.profit` or profit dicts."""
    total_revenue = DECIMAL_ZERO
    total_profit = DECIMAL_ZERO
    lines_with_cost = 0

    for line in lines:
        profit = line if isinstance(line, dict) else getattr(line, 'profit', None)
        if not profit:
            continue
        total_revenue += profit.get('line_revenue', DECIMAL_ZERO)
        line_profit = profit.get('line_profit_amount')
        if line_profit is not None:
            total_profit += line_profit
            lines_with_cost += 1

    return {
        'total_revenue': total_revenue,
        'total_profit_amount': total_profit if lines_with_cost else None,
        'total_profit_percent': (
            safe_profit_percentage(total_profit, total_revenue)
            if lines_with_cost
            else None
        ),
        'lines_with_cost': lines_with_cost,
    }


def _item_attr(item, *names, default=None):
    if isinstance(item, dict):
        for name in names:
            if name in item and item[name] is not None:
                return item[name]
        return default
    for name in names:
        if hasattr(item, name):
            value = getattr(item, name)
            if value is not None:
                return value
    return default


def find_order_lines_sold_below_cost(items):
    """Return commercial lines where net unit price is below known effective cost.

    Free-gift lines (`es_regalo`) are skipped. Lines without a known cost are not
    blocked (we never invent a cost).
    """
    below_cost = []
    for item in items or []:
        if bool(_item_attr(item, 'es_regalo', default=False)):
            continue

        presentacion = _item_attr(item, 'presentacion')
        cost = _item_attr(item, 'cost', 'unit_cost')
        if cost is None:
            cost = resolve_line_cost(presentacion)
        if cost is None:
            continue

        net_unit_price = _item_attr(item, 'net_unit_price', 'precio_unitario')
        if net_unit_price is None:
            profit = build_order_line_profit(
                cost=cost,
                list_price=_item_attr(item, 'precio', 'list_price', 'precio_unitario_lista', default=0),
                quantity=_item_attr(item, 'cantidad', 'quantity', 'cantidad_facturada', default=0),
                descuento_aplicado=bool(_item_attr(item, 'descuento_aplicado', default=False)),
                descuento_monto=_item_attr(item, 'descuento_monto', 'descuento_monto_unitario', default=0),
            )
            net_unit_price = profit['net_unit_price']
            unit_loss = profit['unit_profit_amount']
            line_loss = profit['line_profit_amount']
            quantity = int(_item_attr(item, 'cantidad', 'quantity', 'cantidad_facturada', default=0) or 0)
        else:
            quantity = int(_item_attr(item, 'cantidad', 'quantity', 'cantidad_facturada', default=0) or 0)
            unit_loss = calculate_unit_profit_amount(cost=cost, sale_price=net_unit_price)
            line_loss = (
                _quantize_money(unit_loss * Decimal(str(quantity)))
                if unit_loss is not None
                else None
            )

        net_unit_price = _quantize_money(net_unit_price)
        cost_money = _quantize_money(cost)
        if net_unit_price >= cost_money:
            continue

        product_name = _item_attr(item, 'producto_nombre', 'product', 'product_name', default='')
        presentation_name = _item_attr(item, 'presentacion_nombre', 'presentation', 'presentation_name', default='')
        if not product_name and presentacion is not None:
            product = getattr(presentacion, 'producto', None)
            product_name = getattr(product, 'nombre', '') or ''
            presentation_name = (
                getattr(presentacion, 'nombre_empaque_cliente', None)
                or getattr(presentacion, 'nombre', '')
                or ''
            )

        below_cost.append(
            {
                'item_id': _item_attr(item, 'id', 'item_id'),
                'product': product_name,
                'presentation': presentation_name,
                'quantity': quantity,
                'cost': cost_money,
                'net_unit_price': net_unit_price,
                'unit_profit_amount': unit_loss,
                'line_profit_amount': line_loss,
            }
        )
    return below_cost


def format_below_cost_error_message(lines):
    """Human-readable block message listing products sold below cost."""
    from django.utils.translation import gettext as _

    if not lines:
        return ''

    details = []
    for row in lines:
        label = row.get('product') or _('Product')
        presentation = row.get('presentation') or ''
        if presentation:
            label = f'{label} / {presentation}'
        details.append(
            _('%(product)s: selling $%(net)s (cost $%(cost)s)')
            % {
                'product': label,
                'net': f"{_quantize_money(row.get('net_unit_price')):.2f}",
                'cost': f"{_quantize_money(row.get('cost')):.2f}",
            }
        )

    return _(
        'Cannot continue: one or more products are being sold below cost. '
        'A supervisor authorization is required. %(details)s'
    ) % {'details': '; '.join(details)}
