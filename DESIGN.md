# Fakturama Image-to-Cash Automation — Design Doc


## Objective

From a single order image, produce a saved, verified Order and linked
Invoice in Fakturama — resolving/creating Debtor and Product master data,
applying payment status — without fixed coordinates or a fixed UI layout.

## System overview

```text
order image → extract → validate → open Order → resolve Debtor →
resolve Product+VAT (per line) → complete lines → save+verify Order →
create linked Invoice → apply payment → save+verify Invoice
```

- **Order-first, not data-first.** The Order editor's own address/product
  selector dialogs *are* the existence check — mirrors how a human uses
  Fakturama, and keeps Debtor/Product resolution anchored to the Order
  being built rather than a detached "build data, then attach it" phase.
- **Verify against the saved record, not the open editor.** Every save is
  followed by re-reading the persisted value (re-selecting via a search
  dialog, or the saved-documents list) — some fields can look correct on
  screen without the write actually persisting (see Tradeoffs).

## 1. Image-extraction strategy

**One vision-LLM call to a structured schema, then a deterministic
validation pass — not OCR plus separate parsing.**

- A vision model reads table structure directly (row/column association,
  date/currency normalization) without a per-template layout parser; OCR
  alone recovers characters but not that structure.
- Output schema mirrors Fakturama's own data: order date, external
  reference, debtor + addresses, payment, line items, source totals.
- Money/percentages are extracted as exact decimal strings and parsed as
  fixed-point decimals immediately — never floats.
- Before touching Fakturama, extracted line totals are recomputed
  (qty × price × (1 − discount)) and reconciled against the image's own
  stated totals. A failed reconciliation stops the run for manual review,
  same as everywhere else — no guessing.

## 2. Control-discovery / grounding strategy

**Microsoft UI Automation (UIA) by default, everywhere; one narrow,
documented exception where UIA exposes nothing.**

- Elements are located by **Name + ControlType**, not AutomationId (not
  stable on Fakturama's SWT widgets) and never by absolute coordinates.
  Unlabeled fields are found relative to a nearby labeled element instead.
- **Verify every write** — read a field back immediately and again after a
  short delay before trusting it; a desktop app's internal data-binding can
  silently decouple the visible widget from what's actually persisted.
- **Stop and screenshot rather than guess** on any spec-flagged manual
  review condition (multiple exact matches, conflicting record, a
  just-created record that can't be found again).

**Where UIA doesn't work:** the search-result grids (Debtor/Product/VAT/
Payment Method) and the Order's Items table expose no row/cell structure
through UIA at all — confirmed against the raw accessibility tree, not just
the wrapper API. Scoped fallback for only these widgets:

- **Read rows** via the grid's own clipboard support (select-all, copy,
  parse), since clipboard operations bypass the incomplete accessibility
  layer entirely.
- **Click a cell** via a pixel offset computed fresh from the grid's own
  live-read UIA rectangle every time — never a cached/hardcoded screen
  coordinate, so it still survives the window moving or resizing.

## 3. Workflow design: resolve-or-create, uniformly

Every entity — Debtor, Product, VAT, Payment Method — follows one shape,
matching the spec's own decision pattern:

```text
search exact → EXACT: reuse   → MISSING: create, then search again to confirm
                               → CONFLICT: stop for manual review, never guess
```

- One shape for every entity keeps the manual-review logic consistent and
  easy to reason about.
- Dependencies resolve in UI order: VAT before the Product that needs it,
  so "missing" is never confused with "exists but misconfigured."
- Verification is layered, not a single end check: per-line values as
  written → Order totals before save → persisted record re-confirmed via
  the saved-documents view after save → same shape for the Invoice.

## Tradeoffs

- **Vision-LLM extraction** generalizes across layouts but isn't
  deterministic — mitigated by the reconciliation pass, not blind trust.
- **UIA-first + clipboard fallback** costs more code than a pure-coordinate
  script but survives layout/DPI changes; coordinate use is contained to
  the few widgets with no UIA alternative.
- **Verify-then-trust on every write** costs latency but is the only way to
  catch an app whose internal sync can silently fail — a "write and move
  on" design would look fine in a demo and risk wrong financial data live.
- **Stop-and-ask over auto-resolving ambiguity** trades full automation
  coverage for trustworthiness on what remains automated.
- **Deliberate scope narrowing under the timebox** — e.g. differing
  billing/delivery addresses, item counts beyond a small cap — handles the
  common case completely and routes the uncommon case to manual review
  rather than half-implementing every branch. Specifics in `checklist.md`.

## Appendix: end-to-end flowchart

```text
┌──────────────────────────────────────────────┐
│              INPUT: ORDER IMAGE              │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ Extract + normalize                          │
│                                              │
│ • Order Date                                 │
│ • External Reference                        │
│ • Debtor/contact/addresses                   │
│ • Payment information                       │
│ • Every SKU/item/qty/price/VAT/discount      │
│ • Source totals                              │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
             Extraction usable?
                  /          \
                NO            YES
                │              │
                ▼              ▼
          STOP / REVIEW   OPEN NEW ORDER
                               │
                               ▼
                    Leave generated No.
                    Set Date
                    Set Cust.Ref.
                    Price Mode = Net
                    VAT = With VAT
                               │
                               ▼
                    ┌──────────────────┐
                    │ RESOLVE DEBTOR   │
                    └────────┬─────────┘
                             │
                             ▼
                 Search Company/customer
                             │
              ┌──────────────┼──────────────┐
              │              │              │
           EXACT          NOT FOUND      AMBIGUOUS
              │              │              │
              │              ▼              ▼
              │       CREATE DEBTOR    MANUAL REVIEW
              │              │
              │              ▼
              │       Enter basic details
              │              │
              │              ▼
              │        Enter Main Address
              │              │
              │              ▼
              │      Assign address roles
              │              │
              │              ▼
              │        Miscellaneous
              │              │
              │              ▼
              │     Resolve Payment Method
              │              │
              │     ┌────────┼─────────┐
              │     │        │         │
              │   EXACT    MISSING   CONFLICT
              │     │        │         │
              │     │        ▼         ▼
              │     │    CREATE PM   MANUAL
              │     │        │       REVIEW
              │     │        ▼
              │     │   Save Payment Method
              │     │        │
              │     └────┬───┘
              │          ▼
              │      Select Method
              │          │
              │          ▼
              │      Save Debtor
              │          │
              │          ▼
              │   Return to Order
              │          │
              │          ▼
              │   Search Debtor again
              │          │
              │       Found?
              │      /       \
              │    YES        NO
              │     │          │
              │     │          ▼
              │     │      REVIEW
              │     │
              └─────┴──────────────┐
                                   ▼
                           SELECT DEBTOR
                                   │
                                   ▼
                     Verify Invoice/Delivery
                             addresses
                                   │
                                   ▼
                   ┌────────────────────────┐
                   │ FOR EACH SOURCE ITEM   │
                   └───────────┬────────────┘
                               │
                               ▼
                         Search exact SKU
                               │
                ┌──────────────┼──────────────┐
                │              │              │
              EXACT         NOT FOUND      CONFLICT
                │              │              │
                │              ▼              ▼
                │          CHECK VAT      MANUAL REVIEW
                │              │
                │       Search VAT name
                │              │
                │    ┌─────────┼─────────┐
                │    │         │         │
                │  EXACT    MISSING   CONFLICT
                │    │         │         │
                │    │         ▼         ▼
                │    │    CREATE VAT   MANUAL
                │    │         │       REVIEW
                │    │         ▼
                │    │      SAVE VAT
                │    │         │
                │    └────┬────┘
                │         ▼
                │    CREATE PRODUCT
                │         │
                │    SKU = Item Number
                │    Name = Description
                │    Description = Description
                │    Gross Price =
                │      Net × (1 + VAT/100)
                │    Cost = 0
                │    Stock = 0
                │         │
                │         ▼
                │      SAVE PRODUCT
                │         │
                │         ▼
                │    Return to Order
                │         │
                │         ▼
                │    Search SKU again
                │         │
                │     Appears?
                │     /     \
                │   YES      NO
                │    │        │
                │    │        ▼
                │    │      REVIEW
                │    │
                └────┴────────────┐
                                  ▼
                         SELECT PRODUCT
                                  │
                                  ▼
                          COMPLETE LINE
                                  │
                         Qty = source
                         U.Price = source
                         VAT = source
                         Discount = source
                                  │
                                  ▼
                    Verify line Price =
                      qty × unit price
                      × (1-discount/100)
                                  │
                                  ▼
                         More items?
                          /       \
                        YES        NO
                         │          │
                         └──────────┘
                                    ▼
                          VERIFY COMPLETE ORDER
                                    │
                         Debtor / addresses
                         Every item
                         Overall discount
                         Shipping
                         Total Net
                         VAT
                         Total
                                    │
                                    ▼
                              SAVE ORDER
                                    │
                                    ▼
                         Data → Documents
                                    │
                                    ▼
                           VERIFY SAVED ORDER
                                    │
                        No. / Date / Cust.Ref.
                        State = Open / Total
                                    │
                                    ▼
                         OPEN SAVED ORDER
                                    │
                                    ▼
                  Create follow-up document → Invoice
                                    │
                                    ▼
                           LINKED INVOICE
                                    │
                                    ▼
                   Leave generated invoice fields
                                    │
                                    ▼
                    Verify inherited Order data
                                    │
                                    ▼
                    Verify Payment Method
                                    │
                         Available?
                        /          \
                      NO            YES
                      │              │
                      ▼              ▼
                 MANUAL REVIEW   PAID STATUS?
                                  /       \
                               PAID      NOT PAID
                                │           │
                                ▼           ▼
                           Check paid    Leave clear
                           Payment Date  No fake date
                           = source      No fake value
                           Value =
                           full total
                                │           │
                                └─────┬─────┘
                                      ▼
                                SAVE INVOICE
                                      │
                                      ▼
                               Data → Documents
                                      │
                                      ▼
                          VERIFY INVOICE + ORDER
                                      │
                       Invoice expected state/total
                       Order still Open
                       Same Cust.Ref
                       Same Total
                                      │
                                      ▼
                       Reopen Invoice if needed
                       to verify payment persistence
                                      │
                                      ▼
                               ┌───────────┐
                               │  SUCCESS  │
                               └───────────┘
                                      │
                                      ▼
                            STOP — create nothing else
```
