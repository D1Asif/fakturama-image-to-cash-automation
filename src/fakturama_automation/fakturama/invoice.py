import time
from datetime import date
from decimal import Decimal, InvalidOperation

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    select_combo_option,
    set_segmented_date,
    type_text_via_keyboard,
)
from fakturama_automation.models.order import OrderData
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired

CENTS = Decimal("0.01")


def _format_date(value: date) -> str:
    # Matches Fakturama's own display format (e.g. "Aug 16, 2026"), which
    # does not zero-pad the day. Duplicated from order.py deliberately --
    # this exact formatting is load-bearing for date fields that have
    # proven fragile (see project memory); not worth the risk of a shared
    # import breaking either module.
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def _parse_currency(text: str) -> Decimal:
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise AutomationError(f"Could not parse currency value {text!r}") from exc


def create_linked_invoice(app: FakturamaApp, window):
    """Create the linked Invoice from the still-open, saved Order via its
    own "Create a follow-up document" -> Invoice button.

    Deliberately not the top-toolbar Invoice button -- only the follow-up
    action preserves the Order<->Invoice relationship (per assessment spec).
    """
    invoice_btn = window.child_window(title="Invoice", control_type="Button")
    app.focus()
    invoice_btn.click_input()

    tab = app.wait_for_control(window, title="New Invoice", control_type="Pane")
    log.info("Linked Invoice created")
    return tab


def _find_payment_combo(tab):
    descendants = tab.descendants()
    for i, el in enumerate(descendants):
        ei = el.element_info
        if ei.control_type == "CheckBox" and ei.name == "paid":
            for c in descendants[i + 1 : i + 4]:
                if c.element_info.control_type == "ComboBox":
                    return c
            break
    raise AutomationError("Payment method combo not found on Invoice")


def apply_payment(app: FakturamaApp, window, tab, order: OrderData) -> None:
    """Set the Invoice's payment method and, if PAID, check paid, set the
    payment date, and set/confirm Value = the full Invoice Total.

    Raises ManualReviewRequired if the required payment method isn't
    available (per assessment spec, this is a stop condition here -- the
    method should already have been resolved on the Debtor by this point).
    Leaves paid unchecked and never invents a date/value for UNPAID orders.
    """
    payment_combo = _find_payment_combo(tab)
    current = payment_combo.legacy_properties()["Value"]
    if current != order.payment.method:
        try:
            select_combo_option(app, window, payment_combo, order.payment.method)
        except AutomationError as exc:
            app.take_error_screenshot("payment_method_unavailable_on_invoice")
            raise ManualReviewRequired(
                f"Required payment method {order.payment.method!r} unavailable on Invoice"
            ) from exc

    if order.payment.status != "PAID":
        log.info("Payment status UNPAID, leaving paid unchecked")
        return

    paid_cb = tab.child_window(title="paid", control_type="CheckBox")
    if paid_cb.get_toggle_state() == 0:
        app.focus()
        paid_cb.click_input()
        time.sleep(0.3)

    if order.payment.payment_date is not None:
        expected = _format_date(order.payment.payment_date)
        paid_date_field = find_after_label(tab, "at", "Edit")
        if paid_date_field.get_value() != expected:
            # Same masked DateTime spinner as the Order's Date field --
            # see set_segmented_date()'s docstring for why set_text()
            # looked correct here but silently failed to persist.
            set_segmented_date(paid_date_field, order.payment.payment_date)

    # Value auto-populates to the Invoice Total once "paid" is checked, but
    # that was only ever an observed assumption, never enforced -- read it
    # back and correct it explicitly rather than trust the auto-populate to
    # always fire (e.g. before the totals recalculation has settled).
    value_field = tab.child_window(title="Value", control_type="Edit")
    actual_value = _parse_currency(value_field.get_value())
    if abs(actual_value - order.total) > CENTS:
        # Confirmed live: unlike some other currency fields, this one does
        # not read back with a "$" prefix immediately after typing (only
        # once formatted on save) -- type_text_via_keyboard()'s containment
        # check needs the plain number, not a "$"-prefixed string.
        type_text_via_keyboard(value_field, str(order.total))
        actual_value = _parse_currency(value_field.get_value())
        if abs(actual_value - order.total) > CENTS:
            raise AutomationError(
                f"Could not set Invoice Value to {order.total}: reads back as {actual_value}"
            )

    log.info(
        f"Payment applied: {order.payment.method}, PAID, date={order.payment.payment_date}, "
        f"value={order.total}"
    )


def save_invoice(app: FakturamaApp, window, order: OrderData) -> str:
    """Save the Invoice and return its Fakturama-assigned number (e.g.
    "INV000007"), for the caller to use when verifying the saved record in
    Data > Documents.

    Reads the number back via _find_invoice_elements' content-based search
    (the tab/pane is renamed from "New Invoice" to its assigned number the
    instant it's saved, so a title-based lookup done right after save would
    hit the same stale-reference problem documented there).
    """
    save_btn = window.child_window(title="Save the current contents", control_type="Button")
    app.focus()
    save_btn.click_input()
    app.dismiss_dialog_if_present("Duplicate Contact")

    save_error = app.check_for_save_error()
    if save_error:
        raise AutomationError(
            f"Invoice save failed: {save_error} -- Fakturama's proposed Invoice No. has likely "
            "gone stale (seen after many dirty tabs accumulate in one session); restarting "
            "Fakturama has reliably cleared this"
        )

    log.info("Invoice saved")

    elements = _find_invoice_elements(window, order.external_reference)
    no_field = None
    for i, el in enumerate(elements):
        ei = el.element_info
        if ei.control_type == "Text" and ei.name == "No.":
            for candidate in elements[i + 1 :]:
                cei = candidate.element_info
                if cei.control_type == "Edit":
                    no_field = candidate
                    break
                if cei.control_type == "Text" and cei.name:
                    break
            break

    if no_field is None:
        raise AutomationError("Could not find the Invoice's No. field after save")
    return no_field.get_value()


def _find_invoice_elements(window, external_reference: str):
    """Re-locate the Invoice pane's descendant controls by content rather
    than through the tab's old title.

    create_linked_invoice's tab reference is a lazily-resolved
    WindowSpecification keyed on title="New Invoice" -- valid right after
    creation, but Fakturama renames the tab (and its Pane) to the assigned
    document number (e.g. "INV000002") the moment it's saved, same as the
    Order tab going from "*New Order" to its own PO number. Any lookup
    through the old `tab` reference after save_invoice() re-searches under
    the stale title and fails. Since the invoice number isn't known ahead
    of time, this instead finds the Pane by content: one with both a
    "paid" CheckBox (Order panes don't have one) and a Cust.Ref matching
    this order.

    Several ancestor Panes above the actual Invoice content pane also
    satisfy that (they contain it, plus sibling tabs -- including the
    Order's own pane, which has a matching Cust.Ref too, just no "paid"
    checkbox), so this keeps every match and returns the one with the
    fewest descendants: the most specific, innermost pane, minimizing the
    chance of grabbing a same-named field from a different open tab.

    Returns a flat descendants list rather than the Pane wrapper itself --
    elements from .descendants() are resolved UIAWrapper objects, not
    WindowSpecifications, so they don't support child_window().
    """
    candidates = []
    for el in window.descendants():
        if el.element_info.control_type != "Pane":
            continue
        children = el.descendants()
        has_paid = any(
            c.element_info.control_type == "CheckBox" and c.element_info.name == "paid" for c in children
        )
        if not has_paid:
            continue
        for c in children:
            if c.element_info.control_type == "Edit" and c.element_info.name == "Cust.Ref.":
                if c.get_value() == external_reference:
                    candidates.append(children)
                break

    if not candidates:
        raise AutomationError(f"Could not find Invoice pane for Cust.Ref {external_reference!r}")

    return min(candidates, key=len)


def _find_named(elements, control_type: str, name: str):
    for el in elements:
        if el.element_info.control_type == control_type and el.element_info.name == name:
            return el
    raise AutomationError(f"{name!r} ({control_type}) not found in Invoice pane")


def verify_invoice(window, order: OrderData) -> None:
    """Compare the Invoice's displayed Cust.Ref / Total Net / VAT / Total
    against the extracted values. Raises ManualReviewRequired on mismatch.
    """
    elements = _find_invoice_elements(window, order.external_reference)
    cust_ref_field = _find_named(elements, "Edit", "Cust.Ref.")
    actual_ref = cust_ref_field.get_value()
    if actual_ref != order.external_reference:
        raise ManualReviewRequired(
            f"Invoice Cust.Ref mismatch: expected {order.external_reference!r}, got {actual_ref!r}"
        )

    total_net_field = _find_named(elements, "Edit", "Total Net")
    vat_field = _find_named(elements, "Edit", "VAT")
    total_field = _find_named(elements, "Edit", "Total")

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
        raise ManualReviewRequired("Invoice totals do not match extracted data: " + "; ".join(mismatches))

    log.info("Invoice verified: Cust.Ref and totals match extracted data")
