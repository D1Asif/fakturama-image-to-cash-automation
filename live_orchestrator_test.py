from datetime import date
from decimal import Decimal

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.models.order import (
    Address,
    DebtorData,
    OrderData,
    OrderItem,
    PaymentData,
)
from fakturama_automation.workflow import orchestrator


# Change this suffix before EVERY run.
TEST_SUFFIX = "20260816-01"

external_reference = f"MANUAL-{TEST_SUFFIX}"
company = f"Automation Test Company {TEST_SUFFIX}"
sku = f"TEST-{TEST_SUFFIX}"

address = Address(
    street="1 Test St",
    zip="12345",
    city="Testville",
    country="United States",
)

order = OrderData(
    order_date=date(2026, 8, 16),
    external_reference=external_reference,
    debtor=DebtorData(
        company=company,
        first_name="Jane",
        last_name="Doe",
        billing_address=address,
        delivery_address=address,
    ),
    payment=PaymentData(
        method="Bank Transfer",
        status="PAID",
        payment_date=date(2026, 8, 16),
    ),
    items=[
        OrderItem(
            sku=sku,
            description=f"Test Widget {TEST_SUFFIX}",
            quantity=Decimal("1"),
            unit_net_price=Decimal("10.00"),
            vat_percentage=Decimal("19"),
        )
    ],
    total_net=Decimal("10.00"),
    vat_total=Decimal("1.90"),
    total=Decimal("11.90"),
)

app = FakturamaApp()
app.connect()

print(f"Running live test: {external_reference}")
print(f"Debtor: {company}")
print(f"SKU: {sku}")

orchestrator.run(app, order)

tabs = [
    control.window_text()
    for control in app.main_window.descendants()
    if control.element_info.control_type == "TabItem"
]
dirty_tabs = [tab for tab in tabs if tab.startswith("*")]

print(f"Dirty tabs: {dirty_tabs}")
print("DONE")