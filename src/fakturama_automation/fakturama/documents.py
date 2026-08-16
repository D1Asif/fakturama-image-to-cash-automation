import time
from decimal import Decimal, InvalidOperation

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import read_grid_rows_via_clipboard
from fakturama_automation.models.order import OrderData
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired

CENTS = Decimal("0.01")

# Fakturama's own internal state codes for this grid, confirmed live (not
# documented anywhere) -- every existing test Order shows
# "COMMAND_ORDER_PENDING" for the (unique, always-present) "open" state, and
# every test Invoice marked paid shows "COMMAND_CHECKED". A genuinely broken
# invoice record (empty Cust.Ref, produced by an earlier bug this session)
# showed "COMMAND_ERROR" -- Fakturama flags it as anomalous too, independent
# confirmation that record really was broken. No confirmed code exists yet
# for an unpaid Invoice's state (no test data available), so that's not
# asserted here.
ORDER_STATE_OPEN = "COMMAND_ORDER_PENDING"
INVOICE_STATE_PAID = "COMMAND_CHECKED"

# Columns, confirmed live via clipboard copy of the Documents grid: icon |
# No. | Date | Name | Cust.Ref. | State | Total | Printed. Date renders as
# a full Java-style timestamp string (e.g. "Sun Aug 16 00:00:00 BDT 2026"),
# not the "Aug 16, 2026" format used elsewhere in the app.
_COL_NO = 1
_COL_DATE = 2
_COL_CUST_REF = 4
_COL_STATE = 5
_COL_TOTAL = 6

# Click point (offset from the Documents pane's own top-left) landing
# inside the results grid -- calibrated live the same way as every other
# grid offset in this project (see project memory: no UIA row/column
# structure exists to locate this semantically).
_GRID_X_OFFSET = 400
_GRID_Y_OFFSET = 100


def _open_documents_tab(app: FakturamaApp, window):
    try:
        tab_item = window.child_window(title="Documents", control_type="TabItem")
        tab_item.wait("visible", timeout=1)
        app.focus()
        tab_item.click_input()
        return
    except Exception:
        pass
    app.click(title="Documents", control_type="Text")


def _read_documents(app: FakturamaApp, window, node_title: str) -> list[list[str]]:
    """Read every row of the Documents grid, filtered to Orders or Invoices
    via the tree node on the left of that pane.

    Same UIA-unreadable-grid problem as the Debtor/Product/VAT/terms-of-
    payment grids (confirmed directly this session: a plain descendants()
    walk over this grid also returns zero DataItem rows despite rendering
    fine on screen) -- uses the same clipboard-copy technique.
    """
    _open_documents_tab(app, window)
    tab = app.wait_for_control(window, title="Documents", control_type="Pane")

    node = window.child_window(title=node_title, control_type="TreeItem")
    app.focus()
    node.click_input()
    time.sleep(0.6)

    rect = tab.rectangle()
    return read_grid_rows_via_clipboard(app, rect.left + _GRID_X_OFFSET, rect.top + _GRID_Y_OFFSET)


def _parse_total(text: str) -> Decimal:
    try:
        return Decimal(text.strip())
    except InvalidOperation as exc:
        raise AutomationError(f"Could not parse Documents-list Total {text!r}") from exc


def _date_matches(cell_text: str, expected) -> bool:
    """Loosely compare the grid's Java-style timestamp cell against a
    date.date, tolerating the time-of-day/timezone portion (which this
    field doesn't carry meaningful information in for this project's
    purposes) -- matches on month abbreviation, day, and year only, parsed
    positionally out of e.g. "Sun Aug 16 00:00:00 BDT 2026".
    """
    parts = cell_text.split()
    if len(parts) < 6:
        return False
    month_abbr, day_str, year_str = parts[1], parts[2], parts[-1]
    try:
        day = int(day_str)
        year = int(year_str)
    except ValueError:
        return False
    return month_abbr == expected.strftime("%b") and day == expected.day and year == expected.year


def verify_order_in_documents(app: FakturamaApp, window, order_no: str, order: OrderData) -> None:
    """Verify the saved Order appears in Data > Documents > Orders with the
    expected No., Date, Cust.Ref, State (open), and Total. Raises
    ManualReviewRequired if it can't be found or any field mismatches.
    """
    rows = _read_documents(app, window, "Orders")
    matches = [r for r in rows if len(r) > _COL_NO and r[_COL_NO].strip() == order_no]

    if not matches:
        raise ManualReviewRequired(f"Saved Order {order_no!r} not found in Data > Documents > Orders")
    if len(matches) > 1:
        raise ManualReviewRequired(f"Multiple Documents rows found for Order {order_no!r}: {matches}")

    row = matches[0]
    mismatches = []

    if row[_COL_CUST_REF].strip() != order.external_reference:
        mismatches.append(f"Cust.Ref: expected {order.external_reference!r}, got {row[_COL_CUST_REF]!r}")
    if not _date_matches(row[_COL_DATE], order.order_date):
        mismatches.append(f"Date: expected {order.order_date}, got {row[_COL_DATE]!r}")
    if row[_COL_STATE].strip() != ORDER_STATE_OPEN:
        mismatches.append(f"State: expected open ({ORDER_STATE_OPEN!r}), got {row[_COL_STATE]!r}")
    actual_total = _parse_total(row[_COL_TOTAL])
    if abs(actual_total - order.total) > CENTS:
        mismatches.append(f"Total: expected {order.total}, got {actual_total}")

    if mismatches:
        raise ManualReviewRequired(
            f"Saved Order {order_no!r} does not match expected data in Documents: " + "; ".join(mismatches)
        )

    log.info(f"Order {order_no!r} verified in Data > Documents > Orders")


def verify_invoice_in_documents(app: FakturamaApp, window, invoice_no: str, order: OrderData) -> None:
    """Verify the saved Invoice appears in Data > Documents > Invoices with
    the expected No., Cust.Ref, Total, and (if the source order was PAID)
    State. Raises ManualReviewRequired if it can't be found or mismatches.
    """
    rows = _read_documents(app, window, "Invoices")
    matches = [r for r in rows if len(r) > _COL_NO and r[_COL_NO].strip() == invoice_no]

    if not matches:
        raise ManualReviewRequired(f"Saved Invoice {invoice_no!r} not found in Data > Documents > Invoices")
    if len(matches) > 1:
        raise ManualReviewRequired(f"Multiple Documents rows found for Invoice {invoice_no!r}: {matches}")

    row = matches[0]
    mismatches = []

    if row[_COL_CUST_REF].strip() != order.external_reference:
        mismatches.append(f"Cust.Ref: expected {order.external_reference!r}, got {row[_COL_CUST_REF]!r}")
    actual_total = _parse_total(row[_COL_TOTAL])
    if abs(actual_total - order.total) > CENTS:
        mismatches.append(f"Total: expected {order.total}, got {actual_total}")
    if order.payment.status == "PAID" and row[_COL_STATE].strip() != INVOICE_STATE_PAID:
        mismatches.append(f"State: expected paid ({INVOICE_STATE_PAID!r}), got {row[_COL_STATE]!r}")

    if mismatches:
        raise ManualReviewRequired(
            f"Saved Invoice {invoice_no!r} does not match expected data in Documents: " + "; ".join(mismatches)
        )

    log.info(f"Invoice {invoice_no!r} verified in Data > Documents > Invoices")


def verify_order_still_open(app: FakturamaApp, window, order_no: str, order: OrderData) -> None:
    """Re-verify the source Order after Invoice creation: still exists,
    still open, same Cust.Ref, same Total (spec section U). Reuses the same
    check as the pre-Invoice verification -- an Order isn't expected to
    change state just because a follow-up Invoice was created from it.
    """
    verify_order_in_documents(app, window, order_no, order)
