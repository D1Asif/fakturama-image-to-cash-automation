from decimal import ROUND_HALF_UP, Decimal

from fakturama_automation.models.order import OrderData, OrderItem
from fakturama_automation.workflow.errors import ManualReviewRequired, ValidationError

TWO_PLACES = Decimal("0.01")


def calculate_line_total(item: OrderItem) -> Decimal:
    return (
        item.quantity
        * item.unit_net_price
        * (Decimal("1") - item.discount_percentage / Decimal("100"))
    ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_gross_price(unit_net_price: Decimal, vat_percentage: Decimal) -> Decimal:
    return (unit_net_price * (Decimal("1") + vat_percentage / Decimal("100"))).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )


def validate_order(order: OrderData) -> None:
    """Deterministic checks that must pass before any Fakturama UI interaction.

    Raises ValidationError for structurally invalid data, ManualReviewRequired
    for data that is well-formed but ambiguous/inconsistent enough to need a
    human to look at it.
    """
    if not order.external_reference:
        raise ValidationError("Missing external_reference (Cust.Ref.)")

    if not order.items:
        raise ValidationError("Order has no items")

    for item in order.items:
        if not item.sku:
            raise ValidationError("Item missing SKU")
        if item.quantity <= 0:
            raise ValidationError(f"Item {item.sku} has non-positive quantity")
        if item.unit_net_price < 0:
            raise ValidationError(f"Item {item.sku} has negative unit_net_price")

    skus = [item.sku for item in order.items]
    if len(skus) != len(set(skus)):
        raise ManualReviewRequired(f"Duplicate SKU in order items: {skus}")

    if order.payment.status == "PAID" and order.payment.payment_date is None:
        raise ManualReviewRequired("Payment marked PAID but no payment_date was extracted")

    computed_net = sum((calculate_line_total(item) for item in order.items), Decimal("0"))
    if abs(computed_net - order.total_net) > TWO_PLACES:
        raise ManualReviewRequired(
            f"Computed net total {computed_net} does not match extracted total_net {order.total_net}"
        )

    computed_total = order.total_net + order.vat_total
    if abs(computed_total - order.total) > TWO_PLACES:
        raise ManualReviewRequired(
            f"total_net + vat_total ({computed_total}) does not match extracted total {order.total}"
        )
