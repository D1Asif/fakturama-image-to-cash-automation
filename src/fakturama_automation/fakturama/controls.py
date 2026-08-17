import time
from datetime import date

import win32con
import win32gui
import win32clipboard
from pywinauto import mouse
from pywinauto.keyboard import send_keys

from fakturama_automation.workflow.errors import AutomationError

# Sentinel written to the clipboard immediately before a select-all+copy
# attempt on a grid. If it's still there afterward, nothing was selected --
# see read_grid_rows_via_clipboard() for why that's how "zero rows" is
# detected here.
_CLIPBOARD_SENTINEL = "__FAKTURAMA_AUTOMATION_EMPTY_SENTINEL__"


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


def set_segmented_date(control, value: date) -> None:
    """Write a date into Fakturama's masked DateTime spinner (Order/Invoice
    Date, Invoice Payment Date) via real per-segment keystrokes.

    This field is not a plain masked text box -- it's a genuine SWT
    DateTime-style spinner with three independently-editable segments
    (month/day/year). Confirmed live that neither of this project's two
    general-purpose text-setting helpers works correctly here:

    - set_text() (ValuePattern.SetValue / WM_SETTEXT) writes the display
      text directly without going through the widget's normal keystroke
      handling. The field *looks* correct immediately -- and even right
      after Save -- but silently reverts to today's date the next time
      anything (e.g. navigating to Data > Documents) makes Fakturama
      re-render from the underlying, never-actually-updated model. This
      was the root cause of the long-standing Date-reversion bug (see
      docs/PROGRESS.md, "Order/Invoice Date field is unstable").
    - type_text_via_keyboard() (select-all, type the whole formatted
      string like "Jul 14, 2026") corrupts it instead, producing garbage
      like "Aug 20, 714" -- the widget doesn't parse a full text string,
      it expects raw digits typed into whichever segment currently has
      focus.

    The correct interaction, confirmed live: click into the field (lands
    on the month segment nearest the click), Home to make sure the cursor
    is on the month segment, then type exactly 2 digits for month, 2 for
    day, 4 for year -- each segment auto-advances to the next once full,
    with no Tab/Right-arrow needed (an earlier attempt using an explicit
    Right-arrow between segments produced wrong results, e.g. year "0014",
    because the auto-advance had already moved past the day segment by
    the time the deliberate Right-arrow fired). This mirrors how a human
    actually operates the control, which is presumably why manually
    created Orders never exhibited the reversion bug.

    Confirmed to fix the actual persistence bug, not just the display:
    the value survives repeated navigation to Data > Documents (the known
    trigger) and survives closing the tab and reopening a fresh editor
    from the Documents list -- i.e. the real persisted database record is
    correct, not just an in-memory tab that happened not to lose focus.
    """
    # click_input() with no coords clicks the control's center, which lands
    # on the day or year segment (not month) once the field already holds
    # a value wider than a fresh empty one -- confirmed live this produces
    # garbage like "Aug 17, 720" when the click lands mid-value instead of
    # on the month segment. Click near the left edge specifically, where
    # the month segment always starts, then Home for extra certainty.
    control.click_input(coords=(8, 8))
    control.type_keys("{HOME}")
    control.type_keys(f"{value.month:02d}")
    control.type_keys(f"{value.day:02d}")
    control.type_keys(f"{value.year:04d}")
    control.type_keys("{ENTER}")

    expected = f"{value.strftime('%b')} {value.day}, {value.year}"
    actual = control.get_value()
    if actual != expected:
        raise AutomationError(f"Failed to set segmented date: expected {expected!r}, got {actual!r}")


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

    Matches on the stripped name: confirmed live that at least one combo's
    ListItems (the payment-code list) all carry an invisible trailing
    space in their accessible Name (e.g. `'Credit card '`), which fails an
    exact-equality match against the clean value callers naturally pass.
    """
    app.focus()
    combo.click_input()

    target = option_name.strip()
    deadline = time.monotonic() + timeout
    item = None
    while time.monotonic() < deadline and item is None:
        for el in window.top_level_parent().descendants():
            ei = el.element_info
            if ei.control_type == "ListItem" and ei.name and ei.name.strip() == target:
                item = el
                break

    if item is None:
        raise AutomationError(f"Dropdown option {option_name!r} not found")
    item.click_input()


def type_select_combo(app, combo, value: str, max_steps: int = 300):
    """Select a ComboBox option, for combos with long lists (e.g. Country)
    where opening the dropdown and searching ListItems is impractical --
    items are virtualized and not all present in the accessibility tree
    until scrolled into view.

    Typing the full value as one type-ahead string is NOT reliable here:
    confirmed deterministically (not flaky -- reproduced 3/3 tries)
    that typing "United States" resolves to "Estonia" with no error
    from the widget. The list's incremental-search buffer appears to
    reset unpredictably partway through a multi-character string being
    typed via type_keys, even with per-character delays added.

    An earlier version of this function typed a short 2-character anchor
    first, to jump near the target alphabetically before single-stepping,
    and that was verified live to work for one case ('Un' -> "United Arab
    Emirates", then 6 Down-steps -> exactly "United States"). It turned out
    not to generalize: live-reproduced for "Germany", three different
    attempts at the same 2-character anchor produced three different
    results across separate runs (auto-stepped itself to "Martinique";
    typing "Ge" as one call did nothing at all; typing 'G' then 'e' with a
    delay between them dropped the 'G' entirely and jumped to "Ecuador",
    as if only the second keystroke registered) -- i.e. genuinely
    non-deterministic, not just unreliable for one string, so there's no
    per-string workaround worth chasing.

    What's actually reliable: plain Down/Up single-stepping with no
    anchor at all, comparing the combo's current value against the target
    string each step (the list is alphabetically sorted, so direction is
    just a string comparison) -- confirmed live walking cleanly through
    12+ consecutive entries with no skips or surprises. Slower (up to the
    full list length in the worst case, hence the higher default
    max_steps versus the old anchor-assisted version) but deterministic,
    which matters more here than speed.
    """
    app.focus()
    combo.click_input()
    time.sleep(0.3)

    actual = combo.legacy_properties()["Value"]
    steps = 0
    while actual != value and steps < max_steps:
        combo.type_keys("{DOWN}" if actual < value else "{UP}")
        time.sleep(0.08)
        actual = combo.legacy_properties()["Value"]
        steps += 1

    combo.type_keys("{ENTER}")
    time.sleep(0.2)

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


def _get_clipboard_text() -> str | None:
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        return None
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def read_grid_rows_via_clipboard(app, x: int, y: int, attempts: int = 3) -> list[list[str]]:
    """Read every row of a custom-painted SWT results grid (address/product/
    VAT search results, terms-of-payment list, etc.) by clicking into it,
    Select All, Copy, and parsing the clipboard.

    These tables expose zero UIA structure -- confirmed by exhaustive
    testing: a plain descendants() walk, GridPattern/TablePattern queried on
    every Pane, and even a raw *unfiltered* UIA tree walk via comtypes
    (bypassing every filter pywinauto applies) all found nothing beneath the
    Search box, despite the rows rendering fine on screen. Selection and
    clipboard operations work anyway -- Fakturama implements real Copy
    support for these grids (confirmed by tab/comma-separated field data
    coming back, matching visible column order) even though it exposes no
    accessibility tree for them.

    "Zero rows" is detected by writing a sentinel to the clipboard
    immediately before the select-all+copy attempt: an empty grid leaves
    Ctrl+A/Ctrl+C with nothing to select, so the clipboard is left
    untouched (confirmed empirically, including with retries to rule out a
    timing race) -- if the sentinel is still there afterward, there was
    nothing to read.

    A single read has been observed to occasionally under-count rows
    (returned 1 of 2 known-existing matches once, live) for reasons not
    fully root-caused -- possibly a race between the search box's async
    filtering and the click, possibly something else. Retries up to
    `attempts` times and keeps whichever read returned the most rows,
    since under-counting (not over-counting, and not fabricating rows) is
    the only failure mode observed.

    Also defensively dismisses a genuine Fakturama-side bug triggered live
    (twice, reproducibly) by this exact sequence: a native "Internal
    Error" dialog reporting
    `java.lang.NullPointerException: Cannot read the array length because
    "this.copiedCells" is null` -- apparently Fakturama's own Copy
    implementation left in an inconsistent state by a synthetic Ctrl+C.
    Recoverable: every subsequent interaction worked normally once
    dismissed, both times it was hit.

    Returns a list of rows, each row a list of tab-separated field strings
    in the grid's own column order. Caller knows that order per grid (it
    differs per screen) and is responsible for mapping fields to names.
    """
    best: list[list[str]] = []
    for _ in range(attempts):
        _set_clipboard_text(_CLIPBOARD_SENTINEL)
        app.focus()
        mouse.click(button="left", coords=(x, y))
        time.sleep(0.3)
        send_keys("^a")
        time.sleep(0.2)
        send_keys("^c")
        time.sleep(0.35)
        app.dismiss_dialog_if_present("Internal Error", wait=0.2)

        text = _get_clipboard_text()
        if text is None or text == _CLIPBOARD_SENTINEL:
            rows: list[list[str]] = []
        else:
            rows = [line.split("\t") for line in text.split("\r\n") if line]

        if len(rows) > len(best):
            best = rows
        time.sleep(0.15)

    return best


def select_grid_row_by_index(app, x: int, y: int, row_index: int) -> None:
    """Navigate to and select the row at row_index (0-based) in a grid read
    via read_grid_rows_via_clipboard(), using Home + Down x row_index.

    This exists because clipboard-read rows carry no element reference back
    to their on-screen row (only text), so once calling code has picked
    which row is the exact match by its position in the read-back list, it
    needs a separate way to move the grid's actual selection cursor there
    before e.g. clicking OK. Does not copy or confirm -- caller does that.
    """
    app.focus()
    mouse.click(button="left", coords=(x, y))
    time.sleep(0.3)
    send_keys("{HOME}")
    time.sleep(0.15)
    for _ in range(row_index):
        send_keys("{DOWN}")
        time.sleep(0.08)
