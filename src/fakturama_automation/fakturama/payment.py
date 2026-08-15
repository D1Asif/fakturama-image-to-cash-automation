from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import find_after_label, set_text, type_select_combo
from fakturama_automation.utils.logging import log
from fakturama_automation.utils.waits import wait_for_results_stable
from fakturama_automation.workflow.errors import ManualReviewRequired

PAYMENT_CODES = {
    "Bank Transfer": "Credit transfer",
    "Credit Card": "Credit card",
    "SEPA Direct Debit": "SEPA direct debit",
}

PAYMENT_CODE_COMBO_TITLE = "!editorPaymentPaymentcode!"


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


def _read_payment_rows(pane) -> list[dict]:
    rows = []
    for item in pane.descendants():
        if item.element_info.control_type != "DataItem":
            continue
        text = item.window_text() or ",".join(item.texts())
        rows.append({"name": text, "_element": item})
    return rows


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

    wait_for_results_stable(lambda: len(_read_payment_rows(tab)), timeout=10, stable_for=0.7)
    rows = _read_payment_rows(tab)
    exact = [r for r in rows if r["name"].strip() == method.strip()]

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

    code_combo = payment_tab.child_window(title=PAYMENT_CODE_COMBO_TITLE, control_type="ComboBox")
    type_select_combo(app, code_combo, PAYMENT_CODES[method])

    for label in ("Cash discount", "Discount Days", "Net Days"):
        field = payment_tab.child_window(title=label, control_type="Edit")
        set_text(field, "0")

    app.click(title="Save the current contents", control_type="Button")
    log.info(f"Created payment method {method!r}")
    return False
