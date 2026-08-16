import time

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    read_grid_rows_via_clipboard,
    select_combo_option,
    set_text,
)
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired

PAYMENT_CODES = {
    "Bank Transfer": "Credit transfer",
    "Credit Card": "Credit card",
    "SEPA Direct Debit": "SEPA direct debit",
}

PAYMENT_CODE_COMBO_TITLE = "!editorPaymentPaymentcode!"

# Click point (offset from the "terms of payment" pane's own top-left)
# landing inside the results grid below the Search box -- calibrated live
# the same way as the other grid offsets in this project (see project
# memory: no UIA row/column structure exists to locate this semantically).
_GRID_X_OFFSET = 100
_GRID_Y_OFFSET = 260


def _open_terms_of_payment_tab(app: FakturamaApp, window):
    """Switch to the "terms of payment" tab, opening it from the left nav
    only if it isn't already open (clicking the nav item while it's
    already open does not reliably bring it to front).
    """
    try:
        tab_item = window.child_window(title="terms of payment", control_type="TabItem")
        tab_item.wait("visible", timeout=1)
        app.focus()
        tab_item.click_input()
        return
    except Exception:
        pass
    app.click(title="terms of payment", control_type="Text")


def _read_payment_rows(app: FakturamaApp, tab) -> list[list[str]]:
    """Read every row of the terms-of-payment results grid via clipboard
    copy. Columns, confirmed live: Standard | Name | Description | Cash
    discount | Discount Days | Net Days.

    Replaces an earlier DataItem-based descendants() walk that always
    returned zero rows -- this grid has no UIA row structure at all, the
    same limitation confirmed exhaustively for the Debtor/Product/VAT
    grids (see project memory and read_grid_rows_via_clipboard()).
    """
    rect = tab.rectangle()
    x, y = rect.left + _GRID_X_OFFSET, rect.top + _GRID_Y_OFFSET
    return read_grid_rows_via_clipboard(app, x, y)


def ensure_payment_method(app: FakturamaApp, window, method: str) -> bool:
    """Ensure a term of payment named exactly `method` exists in Fakturama.

    Returns True if an exact match already existed, False if it was just
    created. Raises ManualReviewRequired if the search is ambiguous or a
    conflicting definition is found.
    """
    _open_terms_of_payment_tab(app, window)
    tab = app.wait_for_control(window, title="terms of payment", control_type="Pane")

    search = find_after_label(tab, "Search:", "Edit")
    set_text(search, method, verify=False)
    time.sleep(1.2)  # let the async search filter settle before reading

    rows = _read_payment_rows(app, tab)
    exact = [r for r in rows if len(r) >= 2 and r[1].strip() == method.strip()]

    if len(exact) > 1:
        app.take_error_screenshot("ambiguous_payment_method")
        raise ManualReviewRequired(f"Multiple exact payment-method matches for {method!r}")

    if len(exact) == 1:
        log.info(f"Payment method {method!r} already exists")
        return True

    if method not in PAYMENT_CODES:
        raise ManualReviewRequired(f"No payment code mapping known for {method!r}")

    app.click(title="Create a new term of payment", control_type="Button")
    payment_tab = app.wait_for_control(window, title="New Term of Payment", control_type="Pane")

    name = payment_tab.child_window(title="Name", control_type="Edit")
    set_text(name, method)
    description = payment_tab.child_window(title="Description", control_type="Edit")
    set_text(description, method)

    # Confirmed live: this combo's list is NOT alphabetically sorted (it's
    # a fixed 12-entry e-invoice payment-means-code list in a semantic,
    # not alphabetical, order -- 'In cash', 'Credit transfer', 'Debit
    # transfer', 'Bank card', 'Direct debit', 'Credit card', 'Debit card',
    # 'Standing agreement', 'SEPA credit transfer', 'SEPA direct debit', ...).
    # type_select_combo()'s anchor-then-step algorithm assumes alphabetical
    # order to pick a direction and landed on the wrong entry ("Debit card"
    # instead of "SEPA direct debit") when tried here. Since the list is
    # short and fixed (not virtualized/open-ended like Country), the
    # click-and-pick-ListItem approach (select_combo_option, same as the
    # VAT-code combo below) is both correct and simpler -- confirmed live
    # that opening this combo does expose real ListItems, contrary to an
    # earlier note in project memory.
    code_combo = payment_tab.child_window(title=PAYMENT_CODE_COMBO_TITLE, control_type="ComboBox")
    select_combo_option(app, window, code_combo, PAYMENT_CODES[method])

    for label in ("Cash discount", "Discount Days", "Net Days"):
        field = payment_tab.child_window(title=label, control_type="Edit")
        set_text(field, "0")

    app.click(title="Save the current contents", control_type="Button")

    save_error = app.check_for_save_error()
    if save_error:
        raise AutomationError(f"Payment method save failed: {save_error}")

    log.info(f"Created payment method {method!r}")
    return False
