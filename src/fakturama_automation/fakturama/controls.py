import time

import win32con
import win32gui

from fakturama_automation.workflow.errors import AutomationError


def set_text(control, text: str, verify: bool = True) -> None:
    """Write text into an Edit control robustly.

    set_edit_text() (UIA ValuePattern.SetValue) fails outright on some
    fields with a COMError, and click_input()+type_keys() can silently
    no-op on others (confirmed on the Debtor editor's ZIP field: enabled,
    editable, visible, normal rectangle, yet neither approach landed any
    text). The reliable fallback is WM_SETTEXT sent directly to the
    control's native win32 handle, which Fakturama's Edit controls expose
    since Eclipse SWT backs them with real Win32 child windows.

    Some filter/search-style Edit controls (confirmed on the "terms of
    payment" list's Search box) visibly display the written text correctly
    after the WM_SETTEXT fallback, but their UIA Value property still reads
    back empty -- pass verify=False for those rather than treating that
    readback mismatch as a real failure.
    """
    try:
        control.set_edit_text(text)
        if control.get_value() == text:
            return
    except Exception:
        pass

    hwnd = control.element_info.handle
    if not hwnd:
        raise AutomationError(f"Could not set text {text!r}: no native handle to fall back to")
    win32gui.SendMessage(hwnd, win32con.WM_SETTEXT, 0, text)

    if verify and control.get_value() != text:
        raise AutomationError(f"Failed to set text: expected {text!r}, got {control.get_value()!r}")


def type_text_via_keyboard(control, text: str) -> None:
    """Write text by simulating real keystrokes (click, select-all, type).

    set_text()'s ValuePattern/WM_SETTEXT approach updates a control's
    displayed value without necessarily notifying Fakturama's underlying
    data model -- confirmed on the Debtor editor's Company field: it read
    back correctly immediately after set_edit_text() (no error, matching
    get_value()), yet the saved record's Company was blank, both in the
    Debtors list and when reopening the saved tab fresh. Only real
    simulated keystrokes reliably persisted it. Prefer this over set_text()
    for any field where a silent persistence failure would matter and the
    control is confirmed visible/clickable.
    """
    control.click_input()
    control.type_keys("^a{DELETE}")
    control.type_keys(text, with_spaces=True)

    # Some fields (e.g. percentage/currency) auto-append a formatting
    # suffix like "%" or "$" on commit, so check containment, not equality.
    actual = control.get_value()
    if text not in actual:
        raise AutomationError(f"Failed to type text: expected {text!r} in value, got {actual!r}")


def find_after_label(window, label: str, control_type: str = "Edit"):
    """Return the first control of control_type that immediately follows a
    Text label in the window's descendant tree.

    Several Fakturama fields (No., Date, the Net/Gross price-mode combo) are
    exposed to UIA without a Name of their own -- only the adjacent Text
    label carries one. This walks the flattened descendant list forward
    from the label and stops either at the first matching control or at the
    next *named* Text label (whichever comes first), so it won't wander
    past the intended field into an unrelated one.
    """
    descendants = window.descendants()
    for i, el in enumerate(descendants):
        ei = el.element_info
        if ei.control_type == "Text" and ei.name == label:
            for candidate in descendants[i + 1 :]:
                cei = candidate.element_info
                if cei.control_type == control_type:
                    return candidate
                if cei.control_type == "Text" and cei.name:
                    break
            break
    raise AutomationError(f"No {control_type} found after label {label!r}")


def find_n_after_label(window, label: str, control_type: str, n: int):
    """Return the first n controls of control_type following a Text label.

    Some Fakturama fields share one combined label for multiple inputs
    (e.g. "First Name Last Name" precedes two unnamed Edit controls, one
    per field, in reading order; "ZIP - City" likewise precedes two).
    """
    descendants = window.descendants()
    for i, el in enumerate(descendants):
        ei = el.element_info
        if ei.control_type == "Text" and ei.name == label:
            found = []
            for candidate in descendants[i + 1 :]:
                cei = candidate.element_info
                if cei.control_type == control_type:
                    found.append(candidate)
                    if len(found) == n:
                        return found
                elif cei.control_type == "Text" and cei.name:
                    break
            raise AutomationError(f"Only found {len(found)}/{n} {control_type} controls after label {label!r}")
    raise AutomationError(f"Label {label!r} not found")


def select_combo_option(app, window, combo, option_name: str, timeout: float = 5):
    """Choose an item from a ComboBox's dropdown by clicking it directly.

    The ComboBoxWrapper's own .select() (SelectionItemPattern-based) was
    found to corrupt the surrounding form -- after calling it, unrelated
    sibling fields (Cust.Ref., the VAT combo) disappeared from the
    accessibility tree entirely. Opening the dropdown and clicking the
    target ListItem exactly as a user would does not have that problem and
    has been verified not to disturb the rest of the form.
    """
    app.focus()
    combo.click_input()

    deadline = time.monotonic() + timeout
    item = None
    while time.monotonic() < deadline and item is None:
        for el in window.top_level_parent().descendants():
            ei = el.element_info
            if ei.control_type == "ListItem" and ei.name == option_name:
                item = el
                break

    if item is None:
        raise AutomationError(f"Dropdown option {option_name!r} not found")
    item.click_input()


def type_select_combo(app, combo, value: str):
    """Select a ComboBox option by typing it (type-ahead), for combos with
    long lists (e.g. Country) where opening the dropdown and searching
    ListItems is impractical -- items are virtualized and not all present
    in the accessibility tree until scrolled into view.
    """
    app.focus()
    combo.set_focus()
    combo.type_keys(value, with_spaces=True)
    combo.type_keys("{ENTER}")

    actual = combo.legacy_properties()["Value"]
    if actual != value:
        raise AutomationError(f"Combo value mismatch after type-select: expected {value!r}, got {actual!r}")


def find_price_mode_combo(window):
    """Locate the unnamed Net/Gross ComboBox that follows the Date field."""
    descendants = window.descendants()
    date_label_idx = None
    for i, el in enumerate(descendants):
        ei = el.element_info
        if ei.control_type == "Text" and ei.name == "Date":
            date_label_idx = i
            break
    if date_label_idx is None:
        raise AutomationError("Date label not found")

    seen_edit = False
    for el in descendants[date_label_idx + 1 :]:
        ei = el.element_info
        if ei.control_type == "Edit":
            seen_edit = True
            continue
        if ei.control_type == "ComboBox" and seen_edit:
            return el
    raise AutomationError("Price-mode ComboBox not found after Date field")
