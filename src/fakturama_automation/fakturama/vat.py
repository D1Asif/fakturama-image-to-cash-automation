from decimal import Decimal

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    select_combo_option,
    set_text,
    type_text_via_keyboard,
)
from fakturama_automation.utils.logging import log

VAT_CODE_STANDARD = "S (Standard rate)"


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


def ensure_vat(app: FakturamaApp, window, percentage: Decimal) -> str:
    """Ensure a "VAT {percentage}%" tax rate exists, creating it if missing.

    Returns the VAT name (e.g. "VAT 19%") for use in the Product form.
    Known limitation: the VATs search-result grid cannot be read via UIA in
    this Fakturama build (see project memory), so this cannot currently
    detect a conflicting existing definition -- it always creates.
    """
    name = vat_name(percentage)

    _open_vats_tab(app, window)
    tab = app.wait_for_control(window, title="VATs", control_type="Pane")
    search = find_after_label(tab, "Search:", "Edit")
    set_text(search, name, verify=False)

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

    log.info(f"Created VAT {name!r}")
    return name
