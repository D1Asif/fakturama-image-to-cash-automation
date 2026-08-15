import time

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    find_n_after_label,
    select_combo_option,
    set_text,
    type_select_combo,
    type_text_via_keyboard,
)
from fakturama_automation.fakturama.payment import ensure_payment_method
from fakturama_automation.models.order import DebtorData
from fakturama_automation.utils.logging import log
from fakturama_automation.utils.waits import wait_for_results_stable
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired

# Column order in the "Select the address" results grid, per the assessment
# spec's screenshot: No. | First Names | Names | Company | ZIP | City | address...
RESULT_COLUMNS = ["no", "first_name", "last_name", "company", "zip", "city"]


def _address_selector_icons(window):
    descendants = window.descendants()
    for i, el in enumerate(descendants):
        ei = el.element_info
        if ei.control_type == "Text" and ei.name == "Addresses":
            images = [c for c in descendants[i + 1 : i + 4] if c.element_info.control_type == "Image"]
            if len(images) < 2:
                raise AutomationError("Could not find both address-selector icons")
            return images[0], images[1]  # (existing-contact icon, new-debtor icon)
    raise AutomationError("Addresses label not found")


def _read_result_rows(dialog) -> list[dict]:
    rows = []
    for item in dialog.descendants():
        if item.element_info.control_type != "DataItem":
            continue
        cells = [c.window_text() for c in item.children() if c.element_info.control_type == "Text"]
        if not cells:
            cells = item.texts()
        row = dict(zip(RESULT_COLUMNS, cells))
        row["_element"] = item
        rows.append(row)
    return rows


def _is_exact_match(row: dict, debtor: DebtorData) -> bool:
    return (
        row.get("company", "").strip() == debtor.company.strip()
        and row.get("first_name", "").strip() == debtor.first_name.strip()
        and row.get("last_name", "").strip() == debtor.last_name.strip()
        and row.get("zip", "").strip() == debtor.billing_address.zip.strip()
        and row.get("city", "").strip() == debtor.billing_address.city.strip()
    )


def _open_address_selector(app: FakturamaApp, window, attempts: int = 3):
    """Click the upper (existing-contact) address icon and confirm the
    "Select the address" dialog actually opened, retrying the click if not.

    Clicking this small Image icon has been observed to silently miss
    (no error, dialog just doesn't appear) roughly one time in a few,
    seemingly timing-related. A single click_input() is not reliable
    enough to depend on here.
    """
    for attempt in range(1, attempts + 1):
        existing_icon, _ = _address_selector_icons(window)
        app.focus()
        existing_icon.click_input()
        time.sleep(0.7)

        dialog = window.child_window(title="Select the address", control_type="Window")
        try:
            dialog.wait("visible", timeout=3)
            return dialog
        except Exception:
            log.info(f"Address selector did not open on attempt {attempt}/{attempts}, retrying")

    raise AutomationError("Could not open the address selector dialog after retries")


def resolve_debtor(app: FakturamaApp, window, debtor: DebtorData):
    """Search the Order's address selector for an existing exact-match Debtor.

    Returns True if an existing Debtor was found and selected. Returns
    False if none matched and the dialog was cancelled, meaning the caller
    should proceed to create_debtor(). Raises ManualReviewRequired on any
    ambiguous/conflicting result.
    """
    dialog = _open_address_selector(app, window)

    search = find_after_label(dialog, "Search:", "Edit")
    app.focus()
    set_text(search, debtor.company)

    wait_for_results_stable(lambda: len(_read_result_rows(dialog)), timeout=10, stable_for=0.7)
    rows = _read_result_rows(dialog)

    exact_matches = [r for r in rows if _is_exact_match(r, debtor)]

    if len(exact_matches) > 1:
        app.take_error_screenshot("ambiguous_debtor")
        raise ManualReviewRequired(f"Multiple exact Debtor matches for {debtor.company!r}")

    if len(exact_matches) == 1:
        if len(rows) > 1:
            log.info(f"Debtor search for {debtor.company!r} returned {len(rows)} rows, 1 exact match")
        row = exact_matches[0]
        row["_element"].click_input()
        ok = dialog.child_window(title="OK", control_type="Button")
        ok.click_input()
        log.info(f"Selected existing Debtor {debtor.company!r}")
        return True

    cancel = dialog.child_window(title="Cancel", control_type="Button")
    cancel.click_input()
    log.info(f"No existing Debtor found for {debtor.company!r}")
    return False


def create_debtor(app: FakturamaApp, window, debtor: DebtorData, payment_method: str):
    """Open New Contact and populate a new Debtor from extracted data.

    Leaves Customer ID untouched (auto-generated). Resolves (creating if
    necessary) the payment method and selects it on the Debtor. Does not
    save -- the caller saves once, then re-selects the Debtor from the
    still-open Order to confirm it persisted.
    """
    app.click(title="New Contact", control_type="Text")
    tab = app.wait_for_control(window, title="New Debtor", control_type="Pane")

    company = find_after_label(tab, "Company", "Edit")
    type_text_via_keyboard(company, debtor.company)

    first_name, last_name = find_n_after_label(tab, "First Name Last Name", "Edit", 2)
    set_text(first_name, debtor.first_name)
    set_text(last_name, debtor.last_name)

    main_address_tab = tab.child_window(title="Main address", control_type="TabItem")
    main_address_tab.click_input()

    street = find_after_label(tab, "Street", "Edit")
    set_text(street, debtor.billing_address.street)

    zip_field, city_field = find_n_after_label(tab, "ZIP - City", "Edit", 2)
    set_text(zip_field, debtor.billing_address.zip)
    set_text(city_field, debtor.billing_address.city)

    if debtor.billing_address.email:
        email = find_after_label(tab, "E-Mail", "Edit")
        set_text(email, debtor.billing_address.email)

    if debtor.billing_address.telephone:
        telephone = find_after_label(tab, "Telephone", "Edit")
        set_text(telephone, debtor.billing_address.telephone)

    country = tab.child_window(title="Country", control_type="ComboBox")
    type_select_combo(app, country, debtor.billing_address.country)

    app.dismiss_dialog_if_present("Duplicate Contact")

    _assign_address_roles(app, window, tab, delivery_matches_billing=debtor.billing_address == debtor.delivery_address)

    misc_tab = tab.child_window(title="Miscellaneous", control_type="TabItem")
    misc_tab.click_input()

    if debtor.alias:
        alias = tab.child_window(title="Alias name", control_type="Edit")
        set_text(alias, debtor.alias)

    discount = tab.child_window(title="Discount", control_type="Edit")
    set_text(discount, "0")

    net_or_gross = tab.child_window(title="Net or Gross", control_type="ComboBox")
    select_combo_option(app, window, net_or_gross, "Net")

    _select_payment_method(app, window, tab, payment_method)

    log.info(f"New Debtor form populated for {debtor.company!r}")
    return tab


def _select_payment_method(app: FakturamaApp, window, tab, payment_method: str):
    payment_combo = tab.child_window(title="Payment", control_type="ComboBox")
    try:
        select_combo_option(app, window, payment_combo, payment_method)
        return
    except AutomationError:
        pass

    ensure_payment_method(app, window, payment_method)

    # ensure_payment_method navigated to the "terms of payment" tab;
    # switch back to the still-open Debtor tab before retrying selection.
    debtor_tab_item = window.child_window(title="*New Debtor", control_type="TabItem")
    debtor_tab_item.click_input()
    misc_tab = tab.child_window(title="Miscellaneous", control_type="TabItem")
    misc_tab.click_input()

    payment_combo = tab.child_window(title="Payment", control_type="ComboBox")
    select_combo_option(app, window, payment_combo, payment_method)


def _assign_address_roles(app: FakturamaApp, window, tab, delivery_matches_billing: bool):
    """Check Invoice address (and Delivery address, if identical to billing)
    via the "address type" popup on the Main address sub-tab.
    """
    main_address_tab = tab.child_window(title="Main address", control_type="TabItem")
    main_address_tab.click_input()
    tab.wheel_mouse_input(wheel_dist=-10)  # address type is below the fold

    btn = find_after_label(tab, "address type", "Button")
    app.focus()
    btn.click_input()

    invoice_cb = _find_checkbox(window, "Invoice address")
    invoice_cb.click_input()

    if delivery_matches_billing:
        delivery_cb = _find_checkbox(window, "Delivery address")
        delivery_cb.click_input()

    window.type_keys("{ESC}")


def _find_checkbox(window, name: str, timeout: float = 5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for el in window.top_level_parent().descendants():
            ei = el.element_info
            if ei.control_type == "CheckBox" and ei.name == name:
                return el
        time.sleep(0.2)
    raise AutomationError(f"CheckBox {name!r} not found (popup may not have opened)")
