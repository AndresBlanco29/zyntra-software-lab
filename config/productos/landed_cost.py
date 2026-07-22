"""Landed Cost helpers: global config + per-presentation overrides."""

from decimal import Decimal, ROUND_HALF_UP

from django.utils.translation import gettext_lazy as _

DECIMAL_ZERO = Decimal('0.00')


def _quantize_money(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def resolve_landed_cost_amount(presentacion):
    """Return the logistics Landed Cost amount for one unit of the presentation."""
    if presentacion is None:
        return DECIMAL_ZERO

    rcost = getattr(presentacion, 'costo', None)
    override_tipo = (getattr(presentacion, 'landed_cost_override_tipo', '') or '').strip().upper()
    override_valor = getattr(presentacion, 'landed_cost_override_valor', None)

    if override_tipo in {'PERCENT', 'FIXED'} and override_valor is not None:
        valor = _quantize_money(override_valor)
        if override_tipo == 'FIXED':
            return max(valor, DECIMAL_ZERO)
        if rcost is None:
            return DECIMAL_ZERO
        return max(_quantize_money(_quantize_money(rcost) * valor / Decimal('100')), DECIMAL_ZERO)

    from config.productos.models import ConfiguracionLandedCost

    config = ConfiguracionLandedCost.obtener()
    valor = _quantize_money(config.valor)
    if valor <= 0:
        return DECIMAL_ZERO
    if config.tipo == ConfiguracionLandedCost.TIPO_FIXED:
        return valor
    if rcost is None:
        return DECIMAL_ZERO
    return max(_quantize_money(_quantize_money(rcost) * valor / Decimal('100')), DECIMAL_ZERO)


def resolve_effective_cost(presentacion):
    """RCost + Landed Cost. Used for commercial margin calculations."""
    if presentacion is None:
        return None
    rcost = getattr(presentacion, 'costo', None)
    if rcost is None:
        return None
    return _quantize_money(_quantize_money(rcost) + resolve_landed_cost_amount(presentacion))


def landed_cost_display_parts(presentacion):
    """Structured cost breakdown for admin / backoffice UIs."""
    rcost = getattr(presentacion, 'costo', None) if presentacion is not None else None
    landed = resolve_landed_cost_amount(presentacion) if presentacion is not None else DECIMAL_ZERO
    effective = resolve_effective_cost(presentacion) if presentacion is not None else None
    return {
        'rcost': rcost,
        'landed_cost': landed,
        'effective_cost': effective,
        'rcost_label': _('RCost'),
        'landed_label': _('Landed Cost'),
        'effective_label': _('Total cost (RCost + Landed)'),
    }
