from datetime import date
from decimal import Decimal, InvalidOperation

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    find_price_mode_combo,
    select_combo_option,
    set_segmented_date,
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

    date_field = find_after_label(window, "Date", "Edit")
    set_segmented_date(date_field, order.order_date)

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
    before saving.

    Date now goes through set_segmented_date() (see controls.py), which
    fixed the actual root cause of the long-standing Date-reversion bug --
    set_text() wrote the display text without syncing Fakturama's
    underlying model, which is why the field always looked right
    immediately but reverted later. This re-check is now cheap insurance
    against drift from some other, not-yet-identified trigger, not the
    primary defense it used to be.
    """
    cust_ref = window.child_window(title="Cust.Ref.", control_type="Edit")
    if cust_ref.get_value() != order.external_reference:
        set_text(cust_ref, order.external_reference)

    expected_date_text = _format_date(order.order_date)
    date_field = find_after_label(window, "Date", "Edit")
    if date_field.get_value() != expected_date_text:
        log.info("Date drifted before save, re-setting")
        set_segmented_date(date_field, order.order_date)

    price_mode = find_price_mode_combo(window)
    if price_mode.legacy_properties()["Value"] != PRICE_MODE_NET:
        select_combo_option(app, window, price_mode, PRICE_MODE_NET)


def switch_to_order_tab(app: FakturamaApp, window):
    """Click back onto the (still-open, not yet saved) New Order tab.

    Needed after any excursion to another editor (Debtor/Product/VAT/etc.)
    that changes which tab is active -- searches like `find_after_label`
    and the address/product selector icons only find the right controls
    when the Order tab is actually the visible one.
    """
    for title in ("*New Order", "New Order"):
        try:
            tab_item = window.child_window(title=title, control_type="TabItem")
            tab_item.wait("visible", timeout=1)
            app.focus()
            tab_item.click_input()
            return
        except Exception:
            continue
    raise AutomationError("Could not find the open New Order tab to switch back to")


def save_order(app: FakturamaApp, window, order: OrderData, attempts: int = 3) -> str:
    """Verify totals, re-assert header fields, and save. Returns the
    Fakturama-assigned Order number (e.g. "PO000029") for the caller to use
    when verifying the saved record in Data > Documents.

    Date was previously observed reverting to today's date from multiple
    distinct triggers (price-mode change, address-dialog interaction, the
    Save click itself, and navigating to Documents and back) -- root-caused
    to Date being written via set_text(), which updates the display text
    without syncing Fakturama's underlying model. Now written via
    set_segmented_date() (real per-segment keystrokes, matching how the
    field's own masked DateTime spinner is actually meant to be operated),
    which fixed the persistence bug at its source: confirmed live that a
    date set this way survives repeated navigation to Data > Documents and
    survives closing and reopening the tab fresh from the database, not
    just an immediate in-editor read. The retry loop below is kept as
    cheap defense in depth against drift from some other trigger, not
    because Date is expected to revert anymore.
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

        save_error = app.check_for_save_error()
        if save_error:
            raise AutomationError(
                f"Order save failed: {save_error} -- Fakturama's proposed Order No. has likely "
                "gone stale (seen after many dirty tabs accumulate in one session); restarting "
                "Fakturama has reliably cleared this. Not retrying: the same stale number would "
                "just fail again."
            )

        date_field = find_after_label(window, "Date", "Edit")
        if date_field.get_value() == expected_date_text:
            date_confirmed = True
            break
        log.info(f"Date reverted after save (attempt {attempt}/{attempts}), correcting and re-saving")

    if not date_confirmed:
        raise AutomationError("Date kept reverting immediately after save; could not persist the correct Order Date")

    log.info("Order saved, Date correct immediately after save")

    no_field = find_after_label(window, "No.", "Edit")
    return no_field.get_value()
