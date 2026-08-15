from datetime import date

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    find_price_mode_combo,
    select_combo_option,
    set_text,
)
from fakturama_automation.models.order import OrderData
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError

PRICE_MODE_NET = "Net"
VAT_MODE_WITH_VAT = "With VAT"


def _format_date(value: date) -> str:
    # Matches Fakturama's own display format (e.g. "Aug 16, 2026"), which
    # does not zero-pad the day.
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def open_new_order(app: FakturamaApp, order: OrderData):
    """Open a New Order editor and populate its header fields.

    Leaves the No. field untouched (Fakturama auto-assigns it) and keeps
    the editor open -- the caller is responsible for resolving the Debtor
    and Products before saving.
    """
    window = app.main_window
    app.click(title="Create: New Order", control_type="Button")

    # Switching price mode triggers a form recalculation that can clobber
    # fields written before it (observed: Date silently reverted to today
    # after selecting the price mode combo) -- so set price mode first,
    # then Cust.Ref and Date last, each confirmed immediately after write.
    app.wait_for_control(window, title="Cust.Ref.", control_type="Edit")

    price_mode = find_price_mode_combo(window)
    select_combo_option(app, window, price_mode, PRICE_MODE_NET)

    vat_combo = window.child_window(title="VAT", control_type="ComboBox")
    current_vat_mode = vat_combo.legacy_properties()["Value"]
    if current_vat_mode != VAT_MODE_WITH_VAT:
        raise AutomationError(
            f"Expected VAT mode {VAT_MODE_WITH_VAT!r} by default, found {current_vat_mode!r}"
        )

    cust_ref = window.child_window(title="Cust.Ref.", control_type="Edit")
    set_text(cust_ref, order.external_reference)

    expected_date_text = _format_date(order.order_date)
    date_field = find_after_label(window, "Date", "Edit")
    set_text(date_field, expected_date_text)

    _verify_header(window, order)
    log.info(f"New Order opened, Cust.Ref set to {order.external_reference}")
    return window


def _verify_header(window, order: OrderData) -> None:
    cust_ref = window.child_window(title="Cust.Ref.", control_type="Edit")
    actual_ref = cust_ref.get_value()
    if actual_ref != order.external_reference:
        raise AutomationError(
            f"Cust.Ref mismatch after set: expected {order.external_reference!r}, got {actual_ref!r}"
        )

    date_field = find_after_label(window, "Date", "Edit")
    actual_date = date_field.get_value()
    expected_date = _format_date(order.order_date)
    if actual_date != expected_date:
        raise AutomationError(f"Date mismatch after set: expected {expected_date!r}, got {actual_date!r}")

    price_mode = find_price_mode_combo(window)
    actual_price_mode = price_mode.legacy_properties()["Value"]
    if actual_price_mode != PRICE_MODE_NET:
        raise AutomationError(
            f"Price mode mismatch after set: expected {PRICE_MODE_NET!r}, got {actual_price_mode!r}"
        )
