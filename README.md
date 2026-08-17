# Fakturama Image-to-Cash Automation

Turns a single order image into a fully saved, verified **Order** and linked
**Invoice** inside [Fakturama](https://www.fakturama.info/) — extracting the
source data with a vision LLM, then driving Fakturama's own UI (via
Microsoft UI Automation) to resolve or create the Debtor and Product master
records, build the Order, generate the linked Invoice, and apply the
extracted payment status, verifying each step against the saved record
before moving to the next.

See [`DESIGN.md`](DESIGN.md)
for the design rationale (Part 1) and [`checklist.md`](checklist.md) for
item-by-item status against the functional spec (Part 2).

## Status

- Full pipeline built and **live-verified end-to-end**: Order → Debtor
  (search/create) → Product (search/create, per line) → VAT (search/create)
  → Payment Method (search/create) → Order save/verify → linked Invoice →
  payment application → Invoice save/verify.
- Every exact-match / create / conflict branch has been exercised,
  including multi-item orders.
- Nothing is marked done without being re-verified against the actually
  *saved* Fakturama record, not just an in-session UI read.
- `main.py` (image in → extraction → validation → the orchestrator, end to
  end) has been run against a real order image and a live OpenAI key.

Full item-by-item status: [`checklist.md`](checklist.md).

## Pipeline

```text
order image
    │
    ▼
extraction/extractor.py       -- vision model reads the image, returns a
    │                             structured OrderData (Pydantic)
    ▼
validation/order_validator.py -- Decimal-based checks: totals reconcile,
    │                             required fields present, no float math
    ▼
workflow/orchestrator.py      -- drives the whole Order-first flow, phase by
    │                             phase, against a live Fakturama window
    ▼
fakturama/*.py                 -- one module per Fakturama entity, each doing
                                   search → exact-match/missing/conflict →
                                   reuse or create → return to Order → verify
```

See `DESIGN.md` for why each layer is built the way it is.

## Source layout

```text
src/fakturama_automation/
├── main.py                    CLI entrypoint: image path in, exit code out
├── extraction/
│   └── extractor.py           OpenAI vision call -> OrderData
├── models/
│   └── order.py                Pydantic models (OrderData, DebtorData, OrderItem, ...)
├── validation/
│   └── order_validator.py     Decimal-based financial/field validation
├── workflow/
│   ├── orchestrator.py        the end-to-end flow, phase by phase
│   └── errors.py              AutomationError / ManualReviewRequired
├── fakturama/
│   ├── app.py                 connect/focus/click primitives
│   ├── controls.py            reusable UIA patterns incl. the clipboard-grid
│   │                          technique and the segmented-date-field writer
│   ├── order.py                Order header, totals check, save+verify
│   ├── debtor.py                Debtor search/create/verify
│   ├── payment.py               Payment Method search/create/verify
│   ├── vat.py                   VAT search/create/verify
│   ├── product.py               Product search/create, Items-table editing
│   ├── invoice.py               linked Invoice creation, payment application
│   └── documents.py             saved-record verification
└── utils/                      logging, wait helpers

tests/                          pytest unit tests (extraction, validation,
                                 Decimal calculations) — no live Fakturama
                                 needed to run these
samples/order.png               sample order image used for testing
```

## Setup

**Requirements**

- Windows (Fakturama's UI and this project's UI Automation calls are
  Windows-specific)
- Python 3.11+
- [Fakturama](https://www.fakturama.info/download/) installed and running,
  with a workspace open
- An OpenAI API key with access to a vision-capable model

**Install**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Dependencies (see `pyproject.toml`): `pywinauto`, `pywin32`, `pydantic`,
`openai`, `pillow`, `python-dotenv`; `pytest` for the dev/test extra.

**Configure**

```powershell
copy .env.example .env
```

Edit `.env` and set:

```
OPENAI_API_KEY=sk-...
```

## Running

1. Launch Fakturama and open the workspace you want the Order created in.
2. With the venv active:

```powershell
python -m fakturama_automation.main path\to\order_image.png
```

The script extracts the order, validates it, connects to the already-running
Fakturama window, and runs the full Order → Invoice flow, printing progress
as it goes.

**Exit codes:**

- `0` — success
- `1` — hard failure (extraction, validation, or an unrecoverable
  automation error)
- `2` — missing arguments
- `3` — `ManualReviewRequired` stop (ambiguous match, a required resource
  that shouldn't be auto-created, or a verification mismatch) — the
  intended behavior for the spec's manual-review paths, not a bug

**Run the unit tests** (no live Fakturama needed):

```powershell
pytest tests/
```

`live_extraction_test.py` and `live_orchestrator_test.py` at the repo root
are development scripts for exercising extraction and the orchestrator
directly, outside the `main.py` CLI wrapper — useful for debugging a single
phase in isolation.

## Known limitations / scope decisions

Full detail and reasoning for each of these is in `checklist.md`'s
Implementation Notes. Summarized:

- **Differing billing/delivery addresses aren't split into two Debtor
  addresses** — only the common case (identical billing/delivery) assigns
  both roles to the one Main address.
- **Additional address fields** (additional name, address specification,
  district) aren't captured by extraction or written to the Debtor.
- **Per-line VAT and Line-Price aren't individually re-confirmed** after
  Product selection — the VAT cell's inline editor and the read-only Price
  cell both proved unreadable through any UI Automation technique tried.
  The Order-level Total Net/VAT/Total check before every save is the
  effective safety net instead.
- **VAT-code (E-Invoice) reuse check is Name+Value only** — the VAT search
  grid doesn't expose that column through any read technique tried.
- **Invoice inheritance verification checks Cust.Ref and totals only** —
  address/VAT-mode/item-line inheritance was manually confirmed correct in
  a clean live test but isn't asserted by an automated check yet.

## Written questions

### If you had 3 more hours, what would you do for this task?

**First priority: manual edge-case testing, not new features.** Time ran
out before every branch of `checklist.md`'s decision tree could be
deliberately forced and watched against live Fakturama data — what's been
run so far skews toward the common cases (one exact Debtor match, one
missing Product, one missing VAT, a single PAID invoice). Not yet exercised
live:

- A genuinely ambiguous Debtor search (2+ exact matches) and a conflicting
  VAT/Payment Method definition — confirming the manual-review stop fires
  cleanly, not just in the cases already tested.
- An UNPAID invoice all the way through save and verify (only PAID has
  been run end-to-end).
- One order that creates a brand-new Debtor, Payment Method, VAT, and
  Product all in the same run, to catch any ordering/state assumption a
  simpler test order wouldn't expose.
- Orders exactly at the 3-item cap boundary (2, 3, and 4 items back to
  back), to confirm the cap and its manual-review fallback trigger at the
  right place.

This is where a UI Automation project's real risk lives: a path that reads
correctly in code but has never actually been clicked through against the
live application.

**Second priority: close the highest-value gaps already flagged in
`checklist.md`**, roughly in order:

1. Extend Items-table editing past the current 3-item cap — real orders
   won't always be that short.
2. Add automated verification of Invoice inheritance (address, VAT mode,
   Order Date), not just Cust.Ref and totals.
3. Re-introduce line-level VAT and Line-Price confirmation on the Order,
   which currently relies on the order-level totals check as a proxy.
4. Support differing billing/delivery addresses (currently only the
   identical-address case assigns the Delivery role).

Lower priority, since no test order has exercised them yet either way:
additional address fields (additional name/specification/district), the
VAT-code (E-Invoice) column check on reuse, and order-level Overall
Discount/Shipping capture.
