from datetime import date
from decimal import Decimal, InvalidOperation

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    find_price_mode_combo,
    select_combo_option,
    set_text,
)
from fakturama_automation.models.order import OrderData
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired

PRICE_MODE_NET = "Net"
VAT_MODE_WITH_VAT = "With VAT"
CENTS = Decimal("0.01")


def _parse_currency(text: str) -> Decimal:
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise AutomationError(f"Could not parse currency value {text!r}") from exc


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


def verify_order_totals(window, order: OrderData) -> None:
    """Compare the Order's displayed Total Net / VAT / Total against the
    extracted values before saving. Raises ManualReviewRequired on any
    mismatch rather than saving an order that doesn't match the source.
    """
    total_net_field = window.child_window(title="Total Net", control_type="Edit")
    vat_field = window.child_window(title="VAT", control_type="Edit")
    total_field = window.child_window(title="Total", control_type="Edit")

    actual_total_net = _parse_currency(total_net_field.get_value())
    actual_vat = _parse_currency(vat_field.get_value())
    actual_total = _parse_currency(total_field.get_value())

    mismatches = []
    if abs(actual_total_net - order.total_net) > CENTS:
        mismatches.append(f"Total Net: expected {order.total_net}, got {actual_total_net}")
    if abs(actual_vat - order.vat_total) > CENTS:
        mismatches.append(f"VAT: expected {order.vat_total}, got {actual_vat}")
    if abs(actual_total - order.total) > CENTS:
        mismatches.append(f"Total: expected {order.total}, got {actual_total}")

    if mismatches:
        raise ManualReviewRequired("Order totals do not match extracted data: " + "; ".join(mismatches))

    log.info("Order totals verified")


def reassert_header(app: FakturamaApp, window, order: OrderData) -> None:
    """Re-check and, if drifted, re-set Cust.Ref/Date/price mode right
    before saving. Field-write ordering elsewhere already avoids the known
    "recalculation clobbers Date" issue, but this is cheap insurance
    against it recurring from an interaction not yet identified as a
    trigger (see project memory).
    """
    cust_ref = window.child_window(title="Cust.Ref.", control_type="Edit")
    if cust_ref.get_value() != order.external_reference:
        set_text(cust_ref, order.external_reference)

    expected_date_text = _format_date(order.order_date)
    date_field = find_after_label(window, "Date", "Edit")
    if date_field.get_value() != expected_date_text:
        log.info("Date drifted before save, re-setting")
        set_text(date_field, expected_date_text)

    price_mode = find_price_mode_combo(window)
    if price_mode.legacy_properties()["Value"] != PRICE_MODE_NET:
        select_combo_option(app, window, price_mode, PRICE_MODE_NET)


def save_order(app: FakturamaApp, window, order: OrderData, attempts: int = 3):
    """Verify totals, re-assert header fields, and save.

    Date has been observed reverting to today's date from multiple
    distinct triggers (price-mode change, address-dialog interaction, the
    Save click itself). This retries save if Date is wrong immediately
    after clicking Save, but does not chase further reversion caused by
    *subsequent* navigation (e.g. switching to Documents and back) -- that
    was tried and turned out to revert deterministically, not flakily, so
    retrying the same sequence never converges. See the KNOWN LIMITATION
    log message below and project memory for details.
    """
    verify_order_totals(window, order)
    save_btn = window.child_window(title="Save the current contents", control_type="Button")
    expected_date_text = _format_date(order.order_date)

    date_confirmed = False
    for attempt in range(1, attempts + 1):
        reassert_header(app, window, order)

        app.focus()
        save_btn.click_input()
        app.dismiss_dialog_if_present("Duplicate Contact")

        date_field = find_after_label(window, "Date", "Edit")
        if date_field.get_value() == expected_date_text:
            date_confirmed = True
            break
        log.info(f"Date reverted after save (attempt {attempt}/{attempts}), correcting and re-saving")

    if not date_confirmed:
        raise AutomationError("Date kept reverting immediately after save; could not persist the correct Order Date")

    log.info("Order saved, Date correct immediately after save")
    log.info(
        "KNOWN LIMITATION: Date has been observed reverting to today's date again after "
        "further navigation away from and back to this tab (e.g. viewing Documents), even "
        "though it reads correctly right after Save -- not resolved in this session. "
        "Re-verify Date from a fresh connection before treating this Order as final."
    )
