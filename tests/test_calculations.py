from decimal import Decimal

from fakturama_automation.models.order import OrderItem
from fakturama_automation.validation.order_validator import (
    calculate_gross_price,
    calculate_line_total,
)


def test_gross_price_from_net_and_vat():
    assert calculate_gross_price(Decimal("100"), Decimal("19")) == Decimal("119.00")


def test_line_total_with_discount():
    item = OrderItem(
        sku="KB-100",
        description="Keyboard",
        quantity=Decimal("2"),
        unit_net_price=Decimal("100"),
        vat_percentage=Decimal("19"),
        discount_percentage=Decimal("10"),
    )
    assert calculate_line_total(item) == Decimal("180.00")


def test_line_total_without_discount():
    item = OrderItem(
        sku="MS-200",
        description="Mouse",
        quantity=Decimal("3"),
        unit_net_price=Decimal("40"),
        vat_percentage=Decimal("19"),
    )
    assert calculate_line_total(item) == Decimal("120.00")
