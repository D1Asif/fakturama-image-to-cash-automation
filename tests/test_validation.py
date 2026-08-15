from datetime import date
from decimal import Decimal

import pytest

from fakturama_automation.fakturama.payment import PAYMENT_CODES
from fakturama_automation.models.order import Address, DebtorData, OrderData, OrderItem, PaymentData
from fakturama_automation.validation.order_validator import validate_order
from fakturama_automation.workflow.errors import ManualReviewRequired, ValidationError

ADDRESS = Address(street="Main St 1", zip="10117", city="Berlin", country="Germany")


def make_order(**overrides) -> OrderData:
    defaults = dict(
        order_date=date(2026, 7, 14),
        external_reference="WEB-2026-0714-A17",
        debtor=DebtorData(
            company="Northstar Office GmbH",
            first_name="Marta",
            last_name="Klein",
            billing_address=ADDRESS,
            delivery_address=ADDRESS,
        ),
        payment=PaymentData(method="Bank Transfer", status="PAID", payment_date=date(2026, 7, 18)),
        items=[
            OrderItem(
                sku="CHR-ERG-01",
                description="Ergonomic Desk Chair",
                quantity=Decimal("2"),
                unit_net_price=Decimal("250"),
                vat_percentage=Decimal("19"),
                discount_percentage=Decimal("10"),
            )
        ],
        total_net=Decimal("450.00"),
        vat_total=Decimal("85.50"),
        total=Decimal("535.50"),
    )
    defaults.update(overrides)
    return OrderData(**defaults)


def test_payment_mapping():
    assert PAYMENT_CODES["Bank Transfer"] == "Credit transfer"
    assert PAYMENT_CODES["Credit Card"] == "Credit card"
    assert PAYMENT_CODES["SEPA Direct Debit"] == "SEPA direct debit"


def test_valid_order_passes():
    validate_order(make_order())


def test_missing_sku_fails():
    order = make_order()
    order.items[0].sku = ""
    with pytest.raises(ValidationError):
        validate_order(order)


def test_empty_items_fails():
    order = make_order(items=[])
    with pytest.raises(ValidationError):
        validate_order(order)


def test_paid_without_payment_date_requires_review():
    order = make_order(
        payment=PaymentData(method="Bank Transfer", status="PAID", payment_date=None)
    )
    with pytest.raises(ManualReviewRequired):
        validate_order(order)


def test_inconsistent_totals_require_review():
    order = make_order(total=Decimal("999.99"))
    with pytest.raises(ManualReviewRequired):
        validate_order(order)


def test_duplicate_sku_requires_review():
    item2 = OrderItem(
        sku="CHR-ERG-01",
        description="Ergonomic Desk Chair (dup)",
        quantity=Decimal("1"),
        unit_net_price=Decimal("250"),
        vat_percentage=Decimal("19"),
    )
    order = make_order()
    order.items.append(item2)
    with pytest.raises(ManualReviewRequired):
        validate_order(order)
