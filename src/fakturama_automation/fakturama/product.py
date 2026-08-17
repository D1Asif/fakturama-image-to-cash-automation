import re
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pywinauto import mouse

from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.fakturama.controls import (
    find_after_label,
    read_grid_rows_via_clipboard,
    select_combo_option,
    select_grid_row_by_index,
    set_text,
    type_text_via_keyboard,
)
from fakturama_automation.models.order import OrderItem
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired

TWO_PLACES = Decimal("0.01")

# Offsets (from the Items table's inner-pane top-left) that land inside the
# Qty / U.Price / Discount cells of item rows, calibrated against the
# current maximized-window layout. Clicking here activates that cell's
# inline editor -- the table exposes no row/column UIA structure at all
# (see project memory), so there is no semantic way to locate these cells;
# this is a deliberate, documented exception to "no fixed coordinates,"
# only used for this one grid where no alternative exists. (The VAT column
# does not get the same treatment -- clicking it opens an unreadable combo
# state rather than a plain text cell; see complete_order_line's docstring.)
#
# ROW_HEIGHT is likewise empirically measured (row 2's Qty-cell Edit
# appeared exactly 25px below row 1's, confirmed live by probing candidate
# offsets against a real 2-item order), not derived from any UIA layout
# property -- the table has none to read.
ROW1_Y_OFFSET = 37
ROW_HEIGHT = 25
QTY_X_OFFSET = 83
UPRICE_X_OFFSET = 863
DISCOUNT_X_OFFSET = 933

# The Items table's own viewport only ever paints ~3 rows at once
# (ROW1_Y_OFFSET + 3*ROW_HEIGHT already exceeds its fixed pane height), so
# rows beyond the third require genuine scrolling rather than the one-time
# "nudge the scrollbar to force a repaint" fix that reliably handles rows
# 1-3 (see prepare_items_grid_for_editing). Confirmed live that once real
# scrolling is involved, the grid's scroll position isn't stable across
# separate interactions -- e.g. switching tabs away and back resets it,
# and successive clicks within the *same* row's three fields can each
# land on a different actual row than intended, silently. Making that
# reliable would need re-verifying which row is actually being edited by
# content (e.g. via the clipboard-read technique) after every single
# field write, not just position-based targeting -- not implemented, so
# orders beyond this size are deliberately routed to manual review instead
# of risking a silent wrong-row write.
MAX_AUTO_EDITABLE_ITEMS = 3

# Click point (offset from the "Select a product" dialog's own top-left)
# landing inside the results grid below the Search box -- same dialog
# layout/offsets as the debtor selector (see debtor.py), calibrated live.
_GRID_X_OFFSET = 100
_GRID_Y_OFFSET = 120


def _grid_click_point(dialog) -> tuple[int, int]:
    rect = dialog.rectangle()
    return rect.left + _GRID_X_OFFSET, rect.top + _GRID_Y_OFFSET


def _product_selector_icon(window):
    descendants = window.descendants()
    for i, el in enumerate(descendants):
        ei = el.element_info
        if ei.control_type == "Text" and ei.name == "Items":
            images = [c for c in descendants[i : i + 6] if c.element_info.control_type == "Image"]
            if not images:
                raise AutomationError("Could not find the product-selector icon")
            return images[0]
    raise AutomationError("Items label not found")


def _open_product_selector(app: FakturamaApp, window, attempts: int = 3):
    """Click the upper (product-selection) icon beside the Items table and
    confirm the "Select a product" dialog opened, retrying if not (the
    same click-can-silently-miss behavior observed on the address icon).
    """
    for attempt in range(1, attempts + 1):
        icon = _product_selector_icon(window)
        app.focus()
        icon.click_input()
        time.sleep(0.7)

        dialog = window.child_window(title="Select a product", control_type="Window")
        try:
            dialog.wait("visible", timeout=3)
            return dialog
        except Exception:
            log.info(f"Product selector did not open on attempt {attempt}/{attempts}, retrying")

    raise AutomationError("Could not open the product selector dialog after retries")


def resolve_product(app: FakturamaApp, window, item: OrderItem) -> bool:
    """Search the Order's product selector for an exact SKU match.

    Returns True if found (and already added to the Order), False if
    missing (dialog cancelled). Raises ManualReviewRequired if more than
    one result has the exact extracted SKU (a genuine data problem --
    duplicate SKUs across Products).

    Root-caused live: when the typed SKU narrows to exactly one match
    *total*, the dialog auto-selects it and closes itself within ~300ms of
    the text being set -- no Down/OK/Cancel needed or possible at that
    point. An earlier version of this function always clicked Cancel
    afterward, which would raise once the dialog was already gone -- i.e.
    it would crash instead of correctly detecting "this SKU already
    exists", exactly the case this function exists to catch.

    If the dialog is still open after searching, the auto-close condition
    wasn't met (zero results, or the SKU search matched more than one row
    e.g. via a looser substring match on other fields) -- read the results
    via clipboard (see controls.read_grid_rows_via_clipboard; this grid has
    no UIA row structure) and check for an exact SKU match among them
    rather than assuming "still open" always means "missing".
    """
    dialog = _open_product_selector(app, window)
    search = find_after_label(dialog, "Search:", "Edit")
    app.focus()
    set_text(search, item.sku, verify=False)
    time.sleep(1.0)

    if not dialog.exists():
        log.info(f"Found existing Product {item.sku!r}, auto-selected into the Order")
        return True

    rows = read_grid_rows_via_clipboard(app, *_grid_click_point(dialog))
    exact_matches = [(i, r) for i, r in enumerate(rows) if r and r[0].strip() == item.sku]

    if len(exact_matches) > 1:
        app.take_error_screenshot("conflicting_product_sku")
        raise ManualReviewRequired(f"Multiple Products found with exact SKU {item.sku!r}")

    if len(exact_matches) == 1:
        row_index, _ = exact_matches[0]
        select_grid_row_by_index(app, *_grid_click_point(dialog), row_index)
        ok = dialog.child_window(title="OK", control_type="Button")
        ok.click_input()
        log.info(f"Selected existing Product {item.sku!r} (exact match among {len(rows)} results)")
        return True

    cancel = dialog.child_window(title="Cancel", control_type="Button")
    cancel.click_input()
    log.info(f"No existing Product found for SKU {item.sku!r}")
    return False


def create_product(app: FakturamaApp, window, item: OrderItem, vat_name: str) -> None:
    """Open New product and populate it from an extracted order item.

    VAT must already exist (ensure_vat) so it's selectable here. Gross
    price is quantity-independent: unit_net_price * (1 + vat% / 100),
    without the line discount.
    """
    app.click(title="New product", control_type="Text")
    tab = app.wait_for_control(window, title="New product", control_type="Pane")

    item_number = tab.child_window(title="Item Number", control_type="Edit")
    type_text_via_keyboard(item_number, item.sku)

    name = tab.child_window(title="Name", control_type="Edit")
    type_text_via_keyboard(name, item.description)

    description = tab.child_window(title="Description", control_type="Edit")
    set_text(description, item.description)

    gross_price = (item.unit_net_price * (Decimal("1") + item.vat_percentage / Decimal("100"))).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )
    price_field = find_after_label(tab, "Price (gross)", "Edit")
    type_text_via_keyboard(price_field, str(gross_price))

    cost_price_field = find_after_label(tab, "cost price (net)", "Edit")
    set_text(cost_price_field, "0")

    vat_combo = tab.child_window(title="VAT", control_type="ComboBox")
    select_combo_option(app, window, vat_combo, vat_name)

    stock = tab.child_window(title="Stock", control_type="Edit")
    set_text(stock, "0")

    save_btn = window.child_window(title="Save the current contents", control_type="Button")
    app.focus()
    save_btn.click_input()

    save_error = app.check_for_save_error()
    if save_error:
        raise AutomationError(f"Product save failed: {save_error}")

    log.info(f"Created Product {item.sku!r} (gross price {gross_price})")


def _items_table_pane(window):
    descendants = window.descendants()
    for i, el in enumerate(descendants):
        ei = el.element_info
        if ei.control_type == "Text" and ei.name == "Items":
            for c in descendants[i:]:
                cei = c.element_info
                if cei.control_type == "Pane":
                    return c
            break
    raise AutomationError("Items table pane not found")


def _row_cell_point(window, row_index: int, x_offset: int) -> tuple[int, int]:
    pane = _items_table_pane(window)
    rect = pane.rectangle()
    x = rect.left + x_offset
    y = rect.top + ROW1_Y_OFFSET + (row_index - 1) * ROW_HEIGHT
    return x, y


def _nudge_items_grid_scrollbar(window, max_clicks: int = 15) -> int:
    """Click the Items grid's own vertical-scrollbar "Line down" button
    repeatedly until it disappears (nothing left to scroll) or max_clicks
    is reached. Returns the number of clicks performed.

    Root-caused live: a freshly-added Order line beyond row 1 exists in
    the data model immediately (the Order's own Total Net already
    reflects its default price) but Fakturama simply never paints it --
    not a coordinate-calculation bug, not a timing issue. Waiting longer,
    switching tabs away and back, scrolling by other means, and a window
    restore+maximize cycle were all tried first and none reliably fixed
    it. Clicking this specific scrollbar button does: each click forces
    SWT to lay out one more row, and once every added row has been
    painted this way, plain coordinate clicks work normally against all
    of them (Qty, U.Price, Discount alike) for the rest of the run --
    confirmed against both 2-item and 3-item orders with exact Total Net
    matches and zero retries needed afterward.

    The button is relocated from the *current* Items pane rect on every
    click rather than cached, since nudging shifts the pane's own
    position (the surrounding form reflows as the grid grows).
    """
    clicked = 0
    for _ in range(max_clicks):
        pane = _items_table_pane(window)
        rect = pane.rectangle()
        button = None
        for el in window.descendants():
            ei = el.element_info
            if ei.control_type != "Button" or ei.name != "Line down":
                continue
            r = el.rectangle()
            if rect.right - 5 <= r.left <= rect.right + 40 and rect.top - 10 <= r.top <= rect.bottom + 60:
                button = el
                break
        if button is None:
            break
        button.click_input()
        clicked += 1
        time.sleep(0.5)
    return clicked


def prepare_items_grid_for_editing(app: FakturamaApp, window) -> None:
    """Call once after every line item has been added to the Order (i.e.
    after the resolve_product/create_product + select_product_by_sku pass
    for every item), before calling complete_order_line for any of them.

    See _nudge_items_grid_scrollbar for why this is necessary -- rows
    beyond the first are otherwise never painted, regardless of how long
    complete_order_line waits before its first click.
    """
    app.focus()
    nudged = _nudge_items_grid_scrollbar(window)
    log.info(f"Items grid scrollbar nudged {nudged} time(s) to render all order lines")


_MAX_CELL_EDIT_HEIGHT = 40  # genuine inline cell editors are ~24-25px tall


def _find_cell_edit(window, x: int, y: int):
    """Find the small inline Edit control activated by clicking a table
    cell at (x, y).

    Rejects any Edit taller than a genuine cell editor: root-caused live
    that without this, a click at a row-2 position can silently match the
    much larger Remarks textbox below the table instead of a real (but not
    actually rendered) cell -- both satisfy "Edit control within 15px of
    the target row's top, x within its horizontal bounds", since the
    Remarks box is wide enough to span multiple columns' worth of X
    offsets. That gave false "success": the write really landed, and read
    back correctly, just in the Remarks field, not the intended cell --
    confirmed live via a stray "3" appearing in Remarks after a Qty write
    that otherwise looked entirely successful (no retry, no error).
    """
    for el in window.descendants():
        ei = el.element_info
        if ei.control_type != "Edit":
            continue
        r = el.rectangle()
        if r.bottom - r.top > _MAX_CELL_EDIT_HEIGHT:
            continue
        if abs(r.top - y) <= 15 and r.left <= x <= r.right:
            return el
    return None


def _read_row_cell(app: FakturamaApp, window, x: int, y: int) -> str | None:
    """Click into the cell at (x, y), read its current value, and back out
    via Escape. Returns None if no cell editor appears there at all.
    """
    app.focus()
    mouse.click(button="left", coords=(x, y))
    time.sleep(0.3)
    cell_edit = _find_cell_edit(window, x, y)
    if cell_edit is None:
        return None
    value = cell_edit.get_value()
    window.type_keys("{ESC}")
    time.sleep(0.2)
    return value


def _cell_value_matches(actual: str, expected: str) -> bool:
    """Compare a cell's read-back display text (e.g. "3.00", "USD 40",
    "10.00 %") against the plain value that was typed, tolerating whatever
    formatting Fakturama adds on commit. Numeric comparison via Decimal
    (so "3.00" == "3", "USD 40" == "40.00") with a substring-containment
    fallback -- the same tolerance already used elsewhere in this project
    (type_text_via_keyboard) for fields that auto-append formatting --
    for the rare case either side isn't a plain number.

    Compares by absolute value: confirmed live that the Discount column
    reads back with a leading minus sign representing the reduction (typed
    "10", read back "-10.00 %") even though the write is genuinely
    correct -- confirmed via the Order's own Total Net reflecting the
    discount correctly applied. None of the fields this project writes to
    this table (Qty, U.Price, Discount) are ever meant to be negative, so
    comparing magnitudes only is safe here and isn't hiding a real class of
    error.
    """
    numeric = re.sub(r"[^0-9.\-]", "", actual)
    try:
        return abs(Decimal(numeric)) == abs(Decimal(expected))
    except InvalidOperation:
        return expected in actual


def _edit_row_cell(app: FakturamaApp, window, row_index: int, x_offset: int, value: str, attempts: int = 4):
    """Click into the given row's cell at the given offset, type a new
    value, and verify it actually persisted before returning.

    Single-clicking an editable cell in this table activates an inline
    Edit control in place (confirmed empirically for Qty/U.Price/Discount;
    other columns either aren't editable this way or open a separate
    popup, e.g. Description). The resulting control is located afterward
    by its position, since it has no name. Row position is computed from
    the table pane's rectangle read fresh on every call, so a shifted
    scroll/layout position (observed live between calls) doesn't throw off
    the row math the way a cached rectangle would.

    Two distinct failure modes are handled here, both confirmed live:

    1. Row 2+ can fail to appear at all even though it genuinely exists in
       the Order's data (confirmed: the Order's own Total Net already
       reflected the second item's default price before this ever ran) --
       a real Fakturama grid-repaint bug, not a coordinate-calculation
       error. Scrolling the pane, switching tabs away and back, keyboard
       row-navigation, and probing a wide range of nearby Y-offsets were
       all tried first and none of them revealed the row; a window
       restore+maximize cycle (force_redraw) did, sometimes.
    2. Even when a cell editor *is* found and accepts keystrokes with no
       error, the typed value can silently fail to persist -- confirmed
       live: after a force_redraw-recovered editor accepted "3" with no
       exception, the row's actual Qty stayed at its default "1" instead.
       Same "looks like it worked, didn't persist" bug class already
       documented elsewhere in this project for Company/VAT-Value/
       Product-Price, newly confirmed for this table too. A single write
       is therefore never trusted -- this always re-opens the same cell
       afterward and reads back what actually landed.
    2b. An immediate readback isn't enough either -- confirmed live: a
       readback taken right after commit matched the intended value (no
       retry was even logged), yet the value found in the *saved* record
       minutes later had reverted to the default anyway. This points to a
       delayed reversion (async validation/recalculation on Fakturama's
       side) racing an immediate check, not a one-shot persistence
       failure. So a matching immediate readback is only provisional here
       -- this waits and re-reads a second time before trusting it, and
       only accepts the write if *both* checks agree.

    Neither failure mode has a fix that's been shown to work reliably, so
    this retries the whole click-type-verify cycle, forcing a redraw
    between attempts, and only gives up (raising) after `attempts` tries
    -- silently accepting an unverified write is worse than a slower,
    honest failure, per this project's "never silently guess" principle.
    """
    last_actual: str | None = None
    for attempt in range(attempts):
        x, y = _row_cell_point(window, row_index, x_offset)

        app.focus()
        mouse.click(button="left", coords=(x, y))
        time.sleep(0.4)

        cell_edit = _find_cell_edit(window, x, y)
        if cell_edit is None:
            log.info(f"No cell editor appeared at row {row_index}, offset {x_offset} (attempt {attempt + 1}/{attempts})")
            if attempt < attempts - 1:
                app.force_redraw()
            continue

        cell_edit.click_input()
        cell_edit.type_keys("^a{DELETE}")
        cell_edit.type_keys(value)
        cell_edit.type_keys("{ENTER}")
        time.sleep(0.3)

        # Re-open the same cell to confirm the write actually stuck,
        # rather than trusting that no exception means it did.
        last_actual = _read_row_cell(app, window, x, y)
        if last_actual is not None and _cell_value_matches(last_actual, value):
            # An immediate match is only provisional (see docstring 2b) --
            # give any delayed/async reversion a chance to happen, then
            # check again before actually trusting it.
            time.sleep(1.5)
            confirmed_actual = _read_row_cell(app, window, x, y)
            if confirmed_actual is not None and _cell_value_matches(confirmed_actual, value):
                return
            last_actual = confirmed_actual
            log.info(
                f"Row {row_index} offset {x_offset}: wrote {value!r}, immediate readback matched but "
                f"reverted to {last_actual!r} after settling (attempt {attempt + 1}/{attempts}), retrying"
            )
        else:
            log.info(
                f"Row {row_index} offset {x_offset}: wrote {value!r} but readback was {last_actual!r} "
                f"(attempt {attempt + 1}/{attempts}), retrying"
            )

        if attempt < attempts - 1:
            app.force_redraw()

    raise AutomationError(
        f"Could not verify write to row {row_index}, offset {x_offset}: expected {value!r}, "
        f"last readback was {last_actual!r} after {attempts} attempts"
    )


def complete_order_line(app: FakturamaApp, window, item: OrderItem, row_index: int = 1) -> None:
    """Set Qty, U.Price, and Discount on the given Order item row (1-based,
    matching source order).

    Requires prepare_items_grid_for_editing(app, window) to have been
    called once already, after every line item was added to the Order and
    before this is called for any of them -- otherwise row 2+ generally
    won't be painted yet and every cell click on it will fail to find an
    editor.

    Row position (Qty/U.Price/Discount cells) is computed from
    ROW1_Y_OFFSET + (row_index-1)*ROW_HEIGHT so this works for every line
    of a multi-item order, not just a single-item one -- see module
    docstring on ROW_HEIGHT for how that constant was calibrated.

    Does not click into or read the VAT cell, and does not verify the
    displayed line Price against the expected quantity*price*(1-discount)
    formula -- both were tried and dropped as unreliable:

    - The Price column is read-only and, confirmed by direct probing
      (click, double-click, broad descendant scan), exposes no UIA control
      at all -- no Edit, no ComboBox, nothing with a readable value, the
      same class of limitation documented in project memory for the
      address/product/VAT search grids.
    - The VAT cell looked readable at first (an early version of this
      function clicked it and read an inline Edit's value, e.g. "VAT 19%
      (19.0%)"), but confirmed live this was a race, not a stable read:
      clicking the cell actually opens a live combo-selector (a List
      showing a *default* entry like "Tax-free (0.0%)", not the row's
      real current value), and every read technique tried against it
      (get_value(), window_text(), legacy_properties()["Value"], even a
      clipboard Ctrl+A/Ctrl+C select-all-and-copy) deterministically
      returned nothing once that combo state was open. Earlier apparent
      successes were catching a brief window before the combo reset, not
      a real signal -- retrying more never helped, since the failure was
      deterministic per click, not flaky. Kept blocking a real end-to-end
      run in practice, so removed rather than left as an intermittent
      failure point.

    VAT is inherited automatically from whichever Product was
    selected/created for this line (using the exact VAT ensure_vat()
    resolved for item.vat_percentage earlier in the same run), so it's
    correct in the normal flow without an explicit per-line check. The
    Order-level Total Net/VAT/Total check that runs before save
    (order.py::verify_order_totals) is the effective safety net for both
    Price and VAT correctness instead -- a wrong VAT or Price on any line
    would surface there as a totals mismatch.
    """
    fields = [
        (QTY_X_OFFSET, str(item.quantity)),
        (UPRICE_X_OFFSET, str(item.unit_net_price)),
        (DISCOUNT_X_OFFSET, str(item.discount_percentage)),
    ]
    for x_offset, value in fields:
        _edit_row_cell(app, window, row_index, x_offset, value)

    # Writing a later cell in this row has been observed to silently
    # revert an earlier one, even though _edit_row_cell's own immediate
    # *and* delayed readback both confirmed it correct at the time -- the
    # same "a later action clobbers an earlier field" pattern already
    # documented for the Order Date field (order.py::reassert_header),
    # just discovered here for Qty specifically (confirmed live: Qty
    # verified correctly right after being written, then read back as its
    # untouched default once Discount had also been written). Re-check
    # every field in the row together now that all three are done, and
    # re-write any that drifted, since a per-field check alone isn't
    # sufficient insurance against a cross-field side effect.
    mismatches: list[tuple[int, str, str | None]] = []
    for reassert_attempt in range(3):
        mismatches = []
        for x_offset, value in fields:
            x, y = _row_cell_point(window, row_index, x_offset)
            actual = _read_row_cell(app, window, x, y)
            if actual is None or not _cell_value_matches(actual, value):
                mismatches.append((x_offset, value, actual))

        if not mismatches:
            break

        log.info(f"Row {row_index} drifted after completion (attempt {reassert_attempt + 1}/3): {mismatches}")
        for x_offset, value, _actual in mismatches:
            _edit_row_cell(app, window, row_index, x_offset, value)
    else:
        raise AutomationError(f"Row {row_index} fields kept drifting after 3 reassert passes: {mismatches}")

    log.info(
        f"Completed order line {row_index} for {item.sku!r}: qty={item.quantity}, "
        f"unit_net_price={item.unit_net_price}, discount={item.discount_percentage}%"
    )


def select_product_by_sku(app: FakturamaApp, window, sku: str, attempts: int = 3) -> None:
    """Search the product selector by exact SKU and select the sole result.

    Because the results grid can't be read via UIA (see project memory),
    this can't verify there's exactly one match by inspecting cell text --
    it selects via keyboard (Down, Enter) after a search specific enough
    that, immediately after create_product() for that same SKU, one match
    is the only reasonable outcome. Not a substitute for real exact-match
    verification; used only right after creating the record it's selecting.

    Root-caused live: once the typed SKU narrows to the single exact
    match, the dialog auto-selects it and closes itself within ~300ms of
    the search text being set -- before any Down/OK is needed, sometimes
    before this function even finishes its post-type sleep. An earlier
    version always sent Down then clicked OK regardless, which raised
    once the dialog was already gone and (mis-)triggered a retry that
    reopened the dialog and selected the product a second time -- confirmed
    live, three misdiagnosed retries produced three duplicate order lines
    for the same SKU. So: check for auto-close immediately after typing,
    before touching Down/OK at all; only fall back to Down+OK if the
    dialog is still open (e.g. a looser search with several matches).
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            dialog = _open_product_selector(app, window)
            search = find_after_label(dialog, "Search:", "Edit")
            app.focus()
            set_text(search, sku, verify=False)
            time.sleep(1.0)

            if not dialog.exists():
                log.info(f"Selected Product {sku!r} into the Order (dialog auto-closed on selection)")
                return

            search.type_keys("{DOWN}")
            time.sleep(0.3)

            if not dialog.exists():
                log.info(f"Selected Product {sku!r} into the Order")
                return

            ok = dialog.child_window(title="OK", control_type="Button")
            ok.click_input()
            log.info(f"Selected Product {sku!r} into the Order")
            return
        except Exception as exc:
            last_exc = exc
            log.info(f"Product selector closed unexpectedly (attempt {attempt}/{attempts}), retrying")
            time.sleep(0.5)

    app.take_error_screenshot("product_not_found_after_creation")
    raise ManualReviewRequired(
        f"Newly created Product {sku!r} could not be found again after {attempts} attempts"
    ) from last_exc
