from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(value):
    """1234567.5 -> '1 234 567,50' (espace fine insécable comme séparateur de milliers)."""
    try:
        v = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value
    v = v.quantize(Decimal("1")) if v == v.to_integral_value() else v.quantize(Decimal("0.01"))
    return f"{v:,}".replace(",", "\u202f").replace(".", ",")
