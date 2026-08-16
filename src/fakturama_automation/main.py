import sys
from pathlib import Path

from dotenv import load_dotenv

from fakturama_automation.extraction.extractor import extract_order
from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.validation.order_validator import validate_order
from fakturama_automation.workflow import orchestrator
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        print("Usage: python -m fakturama_automation.main <order_image_path>")
        return 2

    image_path = Path(argv[0])

    print("Fakturama Image-to-Cash\n")

    print("Extracting order...")
    try:
        order = extract_order(image_path)
    except AutomationError as exc:
        print(f"[FAIL] Extraction failed: {exc}")
        return 1
    print(f"[OK] Order {order.external_reference} extracted")
    print(f"[OK] {len(order.items)} product(s) detected")

    try:
        validate_order(order)
    except ManualReviewRequired as exc:
        print(f"[WARN] Manual review required: {exc}")
        return 3
    except AutomationError as exc:
        print(f"[FAIL] Validation failed: {exc}")
        return 1
    print("[OK] Financial validation passed\n")

    print("Connecting to Fakturama...")
    app = FakturamaApp()
    try:
        app.connect()
    except AutomationError as exc:
        print(f"[FAIL] Could not connect to Fakturama: {exc}")
        return 1
    print("[OK] Connected\n")

    try:
        orchestrator.run(app, order)
    except ManualReviewRequired as exc:
        print(f"\n[WARN] MANUAL REVIEW REQUIRED: {exc}")
        print("Screenshot saved to evidence/ for review.")
        return 3
    except AutomationError as exc:
        print(f"\n[FAIL] Automation failed: {exc}")
        print("Screenshot saved to evidence/ for review.")
        return 1

    print(f"\nAutomation completed successfully for Order {order.external_reference}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
