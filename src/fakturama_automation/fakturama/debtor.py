import time

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    find_n_after_label,
    read_grid_rows_via_clipboard,
    select_combo_option,
    select_grid_row_by_index,
    set_text,
    type_select_combo,
    type_text_via_keyboard,
)
from fakturama_automation.models.order import DebtorData
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired

# Column order in the "Select the address" results grid, confirmed live via
# clipboard copy (see read_grid_rows_via_clipboard): No. | First Name |
# Last Name | Company | ZIP | City | AddressType | (two unused fields).
RESULT_COLUMNS = ["no", "first_name", "last_name", "company", "zip", "city", "address_type"]

# Click point (offset from the dialog's own top-left) that lands inside the
# results grid, below the Search box -- calibrated live the same way as the
# Items-table offsets in product.py (see project memory: these tables have
# no UIA row/column structure, so there's no semantic way to locate them).
_GRID_X_OFFSET = 100
_GRID_Y_OFFSET = 120


def _grid_click_point(dialog) -> tuple[int, int]:
    rect = dialog.rectangle()
    return rect.left + _GRID_X_OFFSET, rect.top + _GRID_Y_OFFSET


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


def _read_result_rows(app: FakturamaApp, dialog) -> list[dict]:
    """Read every row of the results grid via clipboard copy.

    Replaces an earlier DataItem-based descendants() walk that always
    returned zero rows -- confirmed by exhaustive live testing (plain
    descendants(), GridPattern/TablePattern, and a raw unfiltered UIA
    tree walk via comtypes) that this grid has no UIA row structure at
    all. See read_grid_rows_via_clipboard() for the technique and its
    known limitations.
    """
    x, y = _grid_click_point(dialog)
    raw_rows = read_grid_rows_via_clipboard(app, x, y)
    return [dict(zip(RESULT_COLUMNS, fields)) for fields in raw_rows]


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
    time.sleep(1.2)  # let the async search filter settle before reading

    rows = _read_result_rows(app, dialog)
    exact_matches = [(i, r) for i, r in enumerate(rows) if _is_exact_match(r, debtor)]

    if len(exact_matches) > 1:
        app.take_error_screenshot("ambiguous_debtor")
        raise ManualReviewRequired(f"Multiple exact Debtor matches for {debtor.company!r}")

    if len(exact_matches) == 1:
        if len(rows) > 1:
            log.info(f"Debtor search for {debtor.company!r} returned {len(rows)} rows, 1 exact match")
        row_index, _ = exact_matches[0]
        x, y = _grid_click_point(dialog)
        select_grid_row_by_index(app, x, y, row_index)
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

    # The Payment combo's option list is populated once when this editor is
    # constructed and does not refresh afterward -- confirmed live: neither
    # reopening the dropdown nor typing into it picks up a payment method
    # created in a different tab after the fact (it silently reverts to the
    # previous value instead). Same class of issue the checklist already
    # accounts for with VAT/Product ("open New product only after the
    # required VAT exists, so it is available in the dropdown"), just not
    # stated for Payment Method. The caller (orchestrator._resolve_debtor)
    # therefore ensures the payment method exists *before* opening this
    # editor at all, so a plain select here is always sufficient -- this
    # function no longer has a create-if-missing fallback.
    payment_combo = tab.child_window(title="Payment", control_type="ComboBox")
    select_combo_option(app, window, payment_combo, payment_method)

    log.info(f"New Debtor form populated for {debtor.company!r}")
    return tab


def save_debtor(app: FakturamaApp, window, attempts: int = 2) -> None:
    """Save the Debtor, defensively correcting a known Fakturama field-
    corruption bug first.

    The Customer ID field (auto-proposed, left otherwise untouched per the
    checklist) can pick up a stray "Un" prefix -- confirmed live, caused by
    nothing more than switching tab focus away and back onto this editor --
    which then fails Fakturama's own "is this the next free ID" validation
    on Save. Same general bug class as the Order/Invoice Date-field
    reversion documented in order.py and docs/PROGRESS.md: a Fakturama-side
    tab-reactivation issue, not something caused by this codebase's own
    field writes, and not fixable by avoiding navigation (switching to the
    Debtor tab at all is what triggers it). Stripping the prefix and
    re-setting the clean value right before Save has been confirmed live
    to resolve it -- mirrors order.py::save_order's re-assert-then-retry
    shape for the equivalent Date problem.
    """
    tab = window.child_window(title="New Debtor", control_type="Pane")
    save_btn = window.child_window(title="Save the current contents", control_type="Button")

    save_error = None
    for attempt in range(1, attempts + 1):
        try:
            cust_id_field = find_after_label(tab, "Customer ID", "Edit")
            current = cust_id_field.get_value()
            if current.startswith("Un"):
                log.info(f"Customer ID corrupted to {current!r} before save, correcting")
                set_text(cust_id_field, current[2:])
        except AutomationError:
            pass  # if the field can't be located, just attempt the save as-is

        app.focus()
        save_btn.click_input()
        app.dismiss_dialog_if_present("Duplicate Contact")

        save_error = app.check_for_save_error()
        if not save_error:
            log.info("Debtor saved")
            return
        log.info(f"Debtor save failed (attempt {attempt}/{attempts}): {save_error}")

    raise AutomationError(
        f"Debtor save failed after {attempts} attempts: {save_error} -- Fakturama's proposed "
        "Customer ID kept getting corrupted even after correcting it before each attempt"
    )


def select_debtor_by_name(app: FakturamaApp, window, debtor: DebtorData, attempts: int = 3) -> None:
    """Search the Order's address selector and select the sole result via
    keyboard, mirroring product.py::select_product_by_sku.

    Searches by last_name, not company: confirmed directly that this
    dialog's search does not reliably match on Company (a search for
    "Northstar" against a debtor whose Company field was genuinely
    "Northstar Office GmbH" returned zero rows), while searching by last
    name reliably returned results. See project memory.

    Used right after create_debtor() + save_debtor() for that same
    debtor, so one match is the only reasonable outcome at that moment.
    Not a substitute for real exact-match verification (see project
    memory on the grid-reading limitation) -- if multiple debtors share
    this last name, the wrong one could be selected.

    Retries the whole sequence if the dialog closes itself mid-interaction
    (observed on the equivalent product-selector dialog; see
    select_product_by_sku's docstring).
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            dialog = _open_address_selector(app, window)
            search = find_after_label(dialog, "Search:", "Edit")
            app.focus()
            set_text(search, debtor.last_name, verify=False)
            time.sleep(1.5)

            search.type_keys("{DOWN}")
            time.sleep(0.3)
            ok = dialog.child_window(title="OK", control_type="Button")
            ok.click_input()
            log.info(f"Selected Debtor {debtor.company!r} ({debtor.last_name!r}) into the Order")
            return
        except Exception as exc:
            last_exc = exc
            log.info(f"Address selector closed unexpectedly (attempt {attempt}/{attempts}), retrying")
            time.sleep(0.5)

    app.take_error_screenshot("debtor_not_found_after_creation")
    raise ManualReviewRequired(
        f"Newly created Debtor {debtor.company!r} could not be found again after {attempts} attempts"
    ) from last_exc


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
