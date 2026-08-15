import time
from decimal import ROUND_HALF_UP, Decimal

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    select_combo_option,
    set_text,
    type_text_via_keyboard,
)
from fakturama_automation.models.order import OrderItem
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError

TWO_PLACES = Decimal("0.01")


def _product_selector_icon(window):
    descendants = window.descendants()
    for i, el in enumerate(descendants):
        ei = el.element_info
        if ei.control_type == "Text" and ei.name == "Items":
            images = [c for c in descendants[i : i + 6] if c.element_info.control_type == "Image"]
            if not images:
                raise AutomationError("Could not find the product-selector icon")
            return images[0]
    raise AutomationError("Items label not found")


def _open_product_selector(app: FakturamaApp, window, attempts: int = 3):
    """Click the upper (product-selection) icon beside the Items table and
    confirm the "Select a product" dialog opened, retrying if not (the
    same click-can-silently-miss behavior observed on the address icon).
    """
    for attempt in range(1, attempts + 1):
        icon = _product_selector_icon(window)
        app.focus()
        icon.click_input()
        time.sleep(0.7)

        dialog = window.child_window(title="Select a product", control_type="Window")
        try:
            dialog.wait("visible", timeout=3)
            return dialog
        except Exception:
            log.info(f"Product selector did not open on attempt {attempt}/{attempts}, retrying")

    raise AutomationError("Could not open the product selector dialog after retries")


def resolve_product(app: FakturamaApp, window, item: OrderItem) -> bool:
    """Search the Order's product selector for an exact SKU match.

    Returns True if found and selected, False if missing (dialog
    cancelled). Known limitation: the results grid cannot be read via UIA
    in this Fakturama build (see project memory), so this cannot currently
    distinguish "genuinely missing" from "present but unreadable" -- it
    always behaves as "missing" and proceeds to create_product().
    """
    dialog = _open_product_selector(app, window)
    search = find_after_label(dialog, "Search:", "Edit")
    app.focus()
    set_text(search, item.sku, verify=False)
    time.sleep(1.5)

    cancel = dialog.child_window(title="Cancel", control_type="Button")
    cancel.click_input()
    log.info(f"No existing Product found for SKU {item.sku!r}")
    return False


def create_product(app: FakturamaApp, window, item: OrderItem, vat_name: str) -> None:
    """Open New product and populate it from an extracted order item.

    VAT must already exist (ensure_vat) so it's selectable here. Gross
    price is quantity-independent: unit_net_price * (1 + vat% / 100),
    without the line discount.
    """
    app.click(title="New product", control_type="Text")
    tab = app.wait_for_control(window, title="New product", control_type="Pane")

    item_number = tab.child_window(title="Item Number", control_type="Edit")
    type_text_via_keyboard(item_number, item.sku)

    name = tab.child_window(title="Name", control_type="Edit")
    type_text_via_keyboard(name, item.description)

    description = tab.child_window(title="Description", control_type="Edit")
    set_text(description, item.description)

    gross_price = (item.unit_net_price * (Decimal("1") + item.vat_percentage / Decimal("100"))).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )
    price_field = find_after_label(tab, "Price (gross)", "Edit")
    type_text_via_keyboard(price_field, str(gross_price))

    cost_price_field = find_after_label(tab, "cost price (net)", "Edit")
    set_text(cost_price_field, "0")

    vat_combo = tab.child_window(title="VAT", control_type="ComboBox")
    select_combo_option(app, window, vat_combo, vat_name)

    stock = tab.child_window(title="Stock", control_type="Edit")
    set_text(stock, "0")

    save_btn = window.child_window(title="Save the current contents", control_type="Button")
    app.focus()
    save_btn.click_input()

    log.info(f"Created Product {item.sku!r} (gross price {gross_price})")


def select_product_by_sku(app: FakturamaApp, window, sku: str) -> None:
    """Search the product selector by exact SKU and select the sole result.

    Because the results grid can't be read via UIA (see project memory),
    this can't verify there's exactly one match by inspecting cell text --
    it selects via keyboard (Down, Enter) after a search specific enough
    that, immediately after create_product() for that same SKU, one match
    is the only reasonable outcome. Not a substitute for real exact-match
    verification; used only right after creating the record it's selecting.
    """
    dialog = _open_product_selector(app, window)
    search = find_after_label(dialog, "Search:", "Edit")
    app.focus()
    set_text(search, sku, verify=False)
    time.sleep(1.5)

    search.type_keys("{DOWN}")
    time.sleep(0.3)
    ok = dialog.child_window(title="OK", control_type="Button")
    ok.click_input()
    log.info(f"Selected Product {sku!r} into the Order")
