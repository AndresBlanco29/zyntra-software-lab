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
