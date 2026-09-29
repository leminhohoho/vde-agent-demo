"""vi-VN display of numeric bindings ("145 ngày", "12,4%"): the value itself is never rounded."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ..formatting import format_value


@pytest.mark.parametrize(
    ("value", "unit", "kwargs", "expected"),
    [
        ("145", "DAY", {}, "145 ngày"),
        ("12.5", "DAY", {}, "12,5 ngày"),
        ("12.40", "PCT", {}, "12,4%"),
        ("12.40", "PCT", {"signed": True}, "+12,4%"),
        ("-3.25", "PCT", {"signed": True}, "-3,3%"),
        ("60.00", "PCT", {}, "60%"),
        ("70", "PCT", {}, "70%"),
        ("40", "COUNT", {"noun": "căn"}, "40 căn"),
        ("1234", "COUNT", {}, "1.234"),
        ("12", "COUNT", {"noun": "tháng"}, "12 tháng"),
        ("4200000000", "VND", {}, "4.200.000.000 đồng"),
        ("67440000", "VND_PER_M2", {}, "67.440.000 đồng/m²"),
        ("14.5", "RATIO", {}, "14,5 lần"),
        ("5", "SCORE", {}, "5/100"),
    ],
)
def test_vi_vn_display(value: str, unit: str, kwargs: dict[str, object], expected: str) -> None:
    assert format_value(Decimal(value), unit, **kwargs) == expected  # type: ignore[arg-type]


def test_half_is_rounded_up_for_display_only() -> None:
    assert format_value(Decimal("0.05"), "PCT") == "0,1%"
    assert format_value(Decimal("0.049"), "PCT") == "0%"
