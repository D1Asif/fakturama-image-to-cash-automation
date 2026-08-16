import time
from decimal import Decimal, InvalidOperation

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    read_grid_rows_via_clipboard,
    select_combo_option,
    set_text,
    type_text_via_keyboard,
)
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired

VAT_CODE_STANDARD = "S (Standard rate)"

# Click point (offset from the VATs pane's own top-left) landing inside the
# results grid below the Search box -- calibrated live the same way as the
# other grid offsets in this project (see project memory: no UIA row/column
# structure exists to locate this semantically).
_GRID_X_OFFSET = 100
_GRID_Y_OFFSET = 280


def vat_name(percentage: Decimal) -> str:
    value = percentage.to_integral_value() if percentage == percentage.to_integral_value() else percentage
    return f"VAT {value}%"


def _open_vats_tab(app: FakturamaApp, window):
    try:
        tab_item = window.child_window(title="VATs", control_type="TabItem")
        tab_item.wait("visible", timeout=1)
        app.focus()
        tab_item.click_input()
        return
    except Exception:
        pass
    app.click(title="VATs", control_type="Text")


def _read_vat_rows(app: FakturamaApp, tab) -> list[list[str]]:
    """Read every row of the VATs results grid via clipboard copy.

    Columns, confirmed live: Standard | Name | Description | Value (Value
    as a raw fraction, e.g. "0.19" for 19%, not "19.00%"). This grid has no
    UIA row structure at all (confirmed by exhaustive testing -- see
    read_grid_rows_via_clipboard()), which is why an earlier version of
    this function always created a new VAT unconditionally instead of
    checking for a reusable one first.
    """
    rect = tab.rectangle()
    x, y = rect.left + _GRID_X_OFFSET, rect.top + _GRID_Y_OFFSET
    return read_grid_rows_via_clipboard(app, x, y)


def ensure_vat(app: FakturamaApp, window, percentage: Decimal) -> str:
    """Ensure a "VAT {percentage}%" tax rate exists, reusing an exact match
    if one does, raising for a same-named-but-different-value conflict, and
    creating one if none exists.

    Note: the grid exposes Name and Value but not the VAT code (E-Invoice)
    column, so an existing row can only be verified on Name+Value here, not
    the full three-field match the spec describes -- every VAT this project
    creates always sets code=S (Standard rate) itself (see the create path
    below), so a Name+Value match in practice also means the code matches,
    but a row created by some other means with a different code would not
    be caught by this check.
    """
    name = vat_name(percentage)
    expected_value = percentage / Decimal("100")

    _open_vats_tab(app, window)
    tab = app.wait_for_control(window, title="VATs", control_type="Pane")
    search = find_after_label(tab, "Search:", "Edit")
    set_text(search, name, verify=False)
    time.sleep(1.2)  # let the async search filter settle before reading

    rows = _read_vat_rows(app, tab)
    name_matches = [r for r in rows if len(r) >= 4 and r[1].strip() == name]

    exact_matches = []
    for row in name_matches:
        try:
            row_value = Decimal(row[3].strip())
        except InvalidOperation:
            continue
        if row_value == expected_value:
            exact_matches.append(row)

    conflicting = [r for r in name_matches if r not in exact_matches]
    if conflicting:
        app.take_error_screenshot("conflicting_vat")
        raise ManualReviewRequired(
            f"VAT {name!r} exists with a different Value than expected {expected_value}: {conflicting}"
        )

    if len(exact_matches) > 1:
        app.take_error_screenshot("ambiguous_vat")
        raise ManualReviewRequired(f"Multiple exact VAT matches for {name!r}")

    if len(exact_matches) == 1:
        log.info(f"VAT {name!r} already exists, reusing")
        return name

    app.click(title="Create a new tax rate", control_type="Button")
    vat_tab = app.wait_for_control(window, title="New TAX Rate", control_type="Pane")

    name_field = vat_tab.child_window(title="Name", control_type="Edit")
    set_text(name_field, name)
    description_field = vat_tab.child_window(title="Description", control_type="Edit")
    set_text(description_field, name)

    code_combo = vat_tab.child_window(title="VAT code (E-Invoice)", control_type="ComboBox")
    select_combo_option(app, window, code_combo, VAT_CODE_STANDARD)

    value_field = vat_tab.child_window(title="Value", control_type="Edit")
    type_text_via_keyboard(value_field, str(percentage))

    save_btn = window.child_window(title="Save the current contents", control_type="Button")
    app.focus()
    save_btn.click_input()

    save_error = app.check_for_save_error()
    if save_error:
        raise AutomationError(f"VAT save failed: {save_error}")

    log.info(f"Created VAT {name!r}")
    return name
