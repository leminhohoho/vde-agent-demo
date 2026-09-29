"""vi-VN display strings for numeric bindings (`NumericBinding.display`, spec §4.2).

Pure. Only the display is rounded (half up); `NumericBinding.value` keeps the exact decimal.
Decimal comma, dot thousands separator. Used by the candidate engine (step 4) and by the
claim_binder (step 8, render.py).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .contracts import BindingUnit

ONE_DECIMAL = Decimal("0.1")
WHOLE = Decimal(1)


def _number(value: Decimal, step: Decimal) -> str:
    rounded = value.quantize(step, rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        rounded = rounded.quantize(WHOLE)
    sign = "-" if rounded < 0 else ""
    whole, _, frac = f"{abs(rounded):f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"{sign}{grouped}" + (f",{frac}" if frac else "")


def format_value(value: Decimal, unit: BindingUnit, *, noun: str | None = None, signed: bool = False) -> str:
    """`noun` overrides the unit's default word (e.g. "căn", "tháng"); `signed` adds "+" to gains."""
    if unit == "PCT":
        text = _number(value, ONE_DECIMAL) + "%"
    elif unit == "DAY":
        text = f"{_number(value, ONE_DECIMAL)} {noun or 'ngày'}"
    elif unit == "COUNT":
        text = _number(value, WHOLE) + (f" {noun}" if noun else "")
    elif unit == "VND":
        text = f"{_number(value, WHOLE)} {noun or 'đồng'}"
    elif unit == "VND_PER_M2":
        text = f"{_number(value, WHOLE)} đồng/m²"
    elif unit == "RATIO":
        text = f"{_number(value, ONE_DECIMAL)} {noun or 'lần'}"
    else:  # SCORE, on the DW's 0–100 scale
        text = f"{_number(value, WHOLE)}/100"
    if signed and value > 0:
        text = "+" + text
    return text
