from decimal import Decimal

from fakturama_automation.fakturama import debtor as debtor_mod
from fakturama_automation.fakturama import documents as documents_mod
from fakturama_automation.fakturama import invoice as invoice_mod
from fakturama_automation.fakturama import order as order_mod
from fakturama_automation.fakturama import payment as payment_mod
from fakturama_automation.fakturama import product as product_mod
from fakturama_automation.fakturama import vat as vat_mod
from fakturama_automation.fakturama.app import FakturamaApp
from fakturama_automation.models.order import OrderData, OrderItem
from fakturama_automation.utils.logging import log
from fakturama_automation.workflow.errors import AutomationError, ManualReviewRequired


def run(app: FakturamaApp, order: OrderData) -> None:
    """Run the full Order-first Image-to-Cash flow against an already
    connected Fakturama instance, using pre-extracted, pre-validated
    OrderData.

    Every phase here has been independently verified against saved
    records (see docs/PROGRESS.md); this assembles them into one
    continuous run. Any AutomationError (including ManualReviewRequired)
    is screenshotted before propagating, so the caller can log/report the
    failure without losing the evidence of what the screen looked like at
    that point.
    """
    window = app.main_window

    if len(order.items) > product_mod.MAX_AUTO_EDITABLE_ITEMS:
        raise ManualReviewRequired(
            f"Order has {len(order.items)} line items; the Items table's editing automation is only "
            f"reliable up to {product_mod.MAX_AUTO_EDITABLE_ITEMS} (see product_mod.MAX_AUTO_EDITABLE_ITEMS "
            "docstring) -- larger orders risk a silent wrong-row write and need manual entry instead."
        )

    try:
        log.info("Opening new Order")
        order_mod.open_new_order(app, order)

        _resolve_debtor(app, window, order)

        vat_names: dict[Decimal, str] = {}
        for row_index, item in enumerate(order.items, start=1):
            log.info(f"Processing SKU {item.sku!r} (row {row_index})")
            if item.vat_percentage not in vat_names:
                vat_names[item.vat_percentage] = vat_mod.ensure_vat(app, window, item.vat_percentage)
                order_mod.switch_to_order_tab(app, window)
            _resolve_product(app, window, item, vat_names[item.vat_percentage])

        # All lines must exist in the Order before editing any of them --
        # prepare_items_grid_for_editing forces Fakturama to paint every
        # row beyond the first (never happens on its own, no matter how
        # long complete_order_line waits before its first click), so
        # editing needs to happen only after every item has been added.
        product_mod.prepare_items_grid_for_editing(app, window)

        for row_index, item in enumerate(order.items, start=1):
            product_mod.complete_order_line(app, window, item, row_index)

        log.info("Saving Order")
        order_no = order_mod.save_order(app, window, order)

        log.info("Verifying saved Order in Documents")
        documents_mod.verify_order_in_documents(app, window, order_no, order)
        _switch_to_tab(app, window, order_no)

        log.info("Creating linked Invoice")
        invoice_tab = invoice_mod.create_linked_invoice(app, window)

        invoice_mod.apply_payment(app, window, invoice_tab, order)

        log.info("Saving Invoice")
        invoice_no = invoice_mod.save_invoice(app, window, order)

        invoice_mod.verify_invoice(window, order)

        log.info("Verifying saved Invoice and source Order in Documents")
        documents_mod.verify_invoice_in_documents(app, window, invoice_no, order)
        documents_mod.verify_order_still_open(app, window, order_no, order)

        log.info(f"SUCCESS: Order {order.external_reference} and its linked Invoice are saved and verified")

    except AutomationError:
        app.take_error_screenshot("workflow_failure")
        raise


def _switch_to_tab(app: FakturamaApp, window, tab_title: str) -> None:
    """Click back onto a document tab by its now-assigned title (e.g. the
    saved Order's own number), for use after an excursion to Documents.

    order.py::switch_to_order_tab only matches "*New Order"/"New Order",
    which no longer exists once the Order has been saved and renamed --
    this covers that later point in the flow.
    """
    tab_item = window.child_window(title=tab_title, control_type="TabItem")
    tab_item.wait("visible", timeout=5)
    app.focus()
    tab_item.click_input()


def _resolve_debtor(app: FakturamaApp, window, order: OrderData) -> None:
    log.info(f"Searching debtor {order.debtor.company!r}")
    found = debtor_mod.resolve_debtor(app, window, order.debtor)
    if found:
        return

    # The New Debtor editor's Payment combo populates its option list once,
    # when the editor is constructed, and never refreshes afterward --
    # confirmed live (see debtor.py::create_debtor). So the payment method
    # must exist *before* opening that editor, not resolved mid-flow the
    # way the checklist's literal wording suggests -- same principle the
    # checklist already applies to VAT/Product ("open New product only
    # after the required VAT exists, so it is available in the dropdown").
    log.info(f"Ensuring payment method {order.payment.method!r} exists before creating debtor")
    payment_mod.ensure_payment_method(app, window, order.payment.method)
    order_mod.switch_to_order_tab(app, window)

    log.info("Creating debtor")
    debtor_mod.create_debtor(app, window, order.debtor, order.payment.method)
    debtor_mod.save_debtor(app, window)
    order_mod.switch_to_order_tab(app, window)
    debtor_mod.select_debtor_by_name(app, window, order.debtor)


def _resolve_product(app: FakturamaApp, window, item: OrderItem, vat_name: str) -> None:
    found = product_mod.resolve_product(app, window, item)
    if found:
        return

    log.info(f"Product {item.sku!r} missing, creating")
    product_mod.create_product(app, window, item, vat_name)
    order_mod.switch_to_order_tab(app, window)
    product_mod.select_product_by_sku(app, window, item.sku)
    log.info(f"Product {item.sku!r} verified")
