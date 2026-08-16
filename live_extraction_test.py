"""Run extraction alone against a sample order image and print every field.

Does not touch Fakturama -- use this to check the vision extraction step in
isolation before trusting it inside the full orchestrator run. Compare the
printed output against the source image by eye.

Usage:
    python live_extraction_test.py [path/to/order/image]

Defaults to samples/order.png if no path is given.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

from fakturama_automation.extraction.extractor import extract_order
from fakturama_automation.validation.order_validator import validate_order
from fakturama_automation.workflow.errors import AutomationError

load_dotenv()

image_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/order.png")

print(f"Extracting from {image_path} ...\n")
try:
    order = extract_order(image_path)
except AutomationError as exc:
    print(f"EXTRACTION FAILED: {exc}")
    raise SystemExit(1)

print("ORDER")
print(f"  order_date          = {order.order_date}")
print(f"  external_reference  = {order.external_reference!r}")

print("\nDEBTOR")
print(f"  company             = {order.debtor.company!r}")
print(f"  first_name          = {order.debtor.first_name!r}")
print(f"  last_name           = {order.debtor.last_name!r}")
print(f"  alias               = {order.debtor.alias!r}")
print("  billing_address:")
b = order.debtor.billing_address
print(f"    street/zip/city/country = {b.street!r}, {b.zip!r}, {b.city!r}, {b.country!r}")
print(f"    email/telephone         = {b.email!r}, {b.telephone!r}")
print("  delivery_address:")
d = order.debtor.delivery_address
print(f"    street/zip/city/country = {d.street!r}, {d.zip!r}, {d.city!r}, {d.country!r}")
print(f"    email/telephone         = {d.email!r}, {d.telephone!r}")

print("\nPAYMENT")
print(f"  method       = {order.payment.method!r}")
print(f"  status       = {order.payment.status!r}")
print(f"  payment_date = {order.payment.payment_date}")

print(f"\nITEMS ({len(order.items)})")
for i, item in enumerate(order.items, 1):
    print(f"  [{i}] sku={item.sku!r} desc={item.description!r}")
    print(
        f"      qty={item.quantity} unit_net_price={item.unit_net_price} "
        f"vat%={item.vat_percentage} discount%={item.discount_percentage} "
        f"source_total={item.source_total}"
    )

print("\nTOTALS")
print(f"  total_net = {order.total_net}")
print(f"  vat_total = {order.vat_total}")
print(f"  total     = {order.total}")

print("\nRunning deterministic validation (financial cross-checks)...")
try:
    validate_order(order)
    print("  OK -- validation passed")
except AutomationError as exc:
    print(f"  FAILED: {exc}")
    raise SystemExit(1)
