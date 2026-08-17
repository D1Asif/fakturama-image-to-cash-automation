# TJM Labs Fakturama Project — Functional Requirements Checklist

**Status as of 2026-08-17.** ✅ = done and, where noted, live-verified
against a real Fakturama instance and its saved records — not just what the
code attempts. ⬜ = not implemented, or implemented with a documented gap.
See `README.md` for setup/run instructions. Footnotes `[N1]`–`[N8]` mark
items that are partially implemented or implemented with a documented
deviation; see **Implementation Notes** below.

## Implementation Notes

- **[N1]** Additional address fields (additional name, address
  specification, district) are not captured by the extraction model or
  written by Debtor creation — a documented scope limitation, not a bug.
- **[N2]** The existing-Debtor reuse and post-creation re-selection paths
  select the matched record but do not run an automated field-by-field diff
  of the Invoice/Delivery address against the source image; a successful
  exact-match search (Company/First Name/Name/ZIP/City) and successful
  re-selection are treated as sufficient evidence, not independently
  re-verified against the image.
- **[N3]** Payment Method resolution actually happens *before* the New
  Debtor editor is opened, not while editing it as the spec's prose
  suggests — the editor's Payment dropdown loads its option list once at
  construction and never refreshes, a real Fakturama behavior found during
  implementation. The functional outcome (payment method exists and is
  selected on the Debtor) is identical; only the sequencing differs.
- **[N4]** VAT reuse compares Name and Value only; the results grid does not
  expose the VAT code (E-Invoice) column through any UIA technique tried, so
  a conflicting code on an otherwise-matching existing row would not be
  caught.
- **[N5]** Per-line VAT confirmation and the Line Price formula check were
  implemented and then deliberately removed: the VAT cell opens a combo
  that resets before any read completes, and the Price cell exposes no UI
  Automation control at all (confirmed by direct probing). The Order-level
  Total Net/VAT/Total check that runs before every save is the safety net
  instead — a wrong line VAT or Price would surface there as a totals
  mismatch.
- **[N6]** Order-level Overall Discount and Shipping are left at Fakturama's
  own defaults (0% / free shipping). The extraction model does not
  currently capture source-supplied order-level discount or shipping
  values, so there is nothing to apply, and no explicit read-back check
  exists either.
- **[N7]** Only Cust.Ref and Total Net/VAT/Total are programmatically
  re-verified on the saved Invoice. Invoice address, Delivery address,
  Order Date, and VAT mode inheritance were manually confirmed correct
  during a clean-environment live test but are not asserted by an
  automated check.
- **[N8]** Multi-item orders are supported for up to 3 line items
  (`MAX_AUTO_EDITABLE_ITEMS`). Orders with 4+ items are routed to manual
  review rather than risk a silent wrong-row write, since Fakturama's Items
  grid only reliably paints 3 rows at a time.

---

## A. Image Extraction Requirements

From the supplied order image, extract and normalize:

### Order

* ✅ Order Date
* ✅ External Reference

### Debtor / Customer

* ✅ Company
* ✅ First Name
* ✅ Last Name / Name
* ✅ Alias
* ✅ Billing address
* ✅ Delivery address
* ✅ Street
* ✅ ZIP
* ✅ City
* ✅ Country
* ✅ E-Mail
* ✅ Telephone
* ⬜ Additional address fields when supplied `[N1]`

### Payment

* ✅ Payment Method
* ✅ Paid Status
* ✅ Payment Date when supplied

### Every product line

* ✅ SKU
* ✅ Description
* ✅ Quantity
* ✅ Unit net price
* ✅ VAT percentage
* ✅ Discount
* ✅ Source total

### Totals

* ✅ Extract enough source totals to verify Fakturama totals later.

---

# B. Open the New Order

* ✅ Click **Order** in Fakturama's top toolbar.
* ✅ Wait for the **New Order** editor.
* ✅ Leave Fakturama's automatically proposed **No.** unchanged.
* ✅ Set **Date** to extracted Order Date.
* ✅ Enter External Reference into **Cust.Ref.**
* ✅ Set document price mode to **Net**.
* ✅ Keep VAT mode as **With VAT**.
* ✅ Keep the **New Order** tab open while resolving any missing:

  * ✅ Debtor
  * ✅ Payment Method `[N3]`
  * ✅ VAT
  * ✅ Product

---

# C. Debtor — Existing Customer Path

## Search

* ✅ From the open Order, click the **upper existing-contact icon** beside Addresses.
* ✅ Do **not** use the lower green `+` for the existence check.
* ✅ Wait for **Select the address**.
* ✅ Search using extracted Company or customer name.
* ✅ Wait for the result list to stabilize.

## Exact-match criteria

A Debtor counts as an exact match only when the visible:

* ✅ Company matches.
* ✅ First Name matches.
* ✅ Name matches.
* ✅ ZIP matches.
* ✅ City matches.

## Branches

### Exactly one exact match

* ✅ Select the exact row.
* ✅ Click **OK**.
* ⬜ Verify Invoice address matches source. `[N2]`
* ⬜ Verify Delivery address matches source. `[N2]`
* ✅ Continue to Product processing.

### No exact match

* ✅ Click **Cancel**.
* ✅ Continue to Debtor creation.

### Conflicting / ambiguous results

* ✅ **Stop for manual review.**
* ✅ Do not guess which customer is correct.

---

# D. Debtor — Creation Path

## Open Debtor editor

* ✅ Keep the Order tab open.
* ✅ Click **New Contact** in the left New panel.
* ✅ Wait for **New Debtor** editor.

## Basic fields

* ✅ Leave Fakturama's proposed **Customer ID** unchanged.
* ✅ Enter Company.
* ✅ Enter First Name.
* ✅ Enter Last Name.
* ✅ Leave Salutation as `---` when none was supplied.

## Main address

Under **Addresses > Main address**:

* ✅ Enter Street.
* ✅ Enter ZIP.
* ✅ Enter City.
* ✅ Enter Country.
* ✅ Enter E-Mail.
* ✅ Enter Telephone.
* ⬜ Enter additional name only when supplied. `[N1]`
* ⬜ Enter address specification only when supplied. `[N1]`
* ⬜ Enter district only when supplied. `[N1]`

## Address roles

* ✅ Assign Main address the **Invoice address** role.
* ✅ If billing and delivery are identical:

  * ✅ Assign the same address the **Delivery address** role.
  * ✅ Do not create a duplicate address.

  *(Differing billing/delivery addresses are a documented, deliberate scope
  limitation — only the identical-address case creates a second role
  assignment.)*

## Miscellaneous

* ✅ Open **Miscellaneous**.
* ✅ Enter Alias name.
* ✅ Set Discount to **0%**.
* ✅ Set Net or Gross to **Net**.

---

# E. Payment Method — Existing Method Path

While still editing the Debtor: `[N3]`

* ✅ Open **Payment**.
* ✅ Attempt to select the exact extracted Payment Method.

If it is available:

* ✅ Select it.
* ✅ Continue with Debtor save.

If it is unavailable:

* ✅ Keep the Debtor editor open. `[N3]`
* ✅ Open **Data > terms of payment**.
* ✅ Search for the exact Payment Method.

### Exactly one unambiguous exact Payment Method exists

* ✅ Return to the Debtor editor.
* ✅ Select it.
* ✅ Skip creation.

### Multiple exact rows / conflicting definition

* ✅ **Stop for manual review.**

### No exact row

* ✅ Create the Payment Method.

---

# F. Payment Method — Creation Path

* ✅ Click the green `+` in terms of payment.
* ✅ Set **Name** to the exact extracted Payment Method.
* ✅ Set **Description** to the exact extracted Payment Method.
* ✅ Leave **Account** blank.

## Required payment-code mapping

* ✅ `Bank Transfer` → `Credit transfer`
* ✅ `Credit Card` → `Credit card`
* ✅ `SEPA Direct Debit` → `SEPA direct debit`

## Remaining settings

* ✅ Cash discount = `0`
* ✅ Discount Days = `0`
* ✅ Net Days = `0`
* ✅ Text `unpaid` = blank
* ✅ Text `deposit` = blank
* ✅ Text `paid` = blank
* ✅ Do **not** click **Set as standard**
* ✅ Click toolbar **Save** once.
* ✅ Return to the open Debtor editor.
* ✅ Select the newly created Payment Method.

---

# G. Finish and Verify New Debtor

* ✅ Save the Debtor **once**.
* ✅ Return to the still-open Order.
* ✅ Reopen **Select the address**.
* ✅ Search for the newly created Debtor again.
* ✅ Select the exact newly created Debtor.
* ✅ Click **OK**.
* ⬜ Verify Invoice address populated correctly. `[N2]`
* ⬜ Verify Delivery address populated correctly. `[N2]`
* ✅ Treat successful re-selection from the Order as confirmation that the Debtor was saved.

---

# H. Product Processing — Repeat for Every Item

* ✅ Process **every extracted item row**. `[N8]`
* ✅ Preserve **source order**.

For each item, execute the complete Product branch below.

---

# I. Product — Existing Product Path

From the open Order:

* ✅ Click the upper **Product-selection icon** beside Items.
* ✅ Do **not** use the green `+` for the existence check.
* ✅ Wait for **Select a product**.
* ✅ Search the **exact extracted SKU**.

### One exact SKU exists

* ✅ Select it.
* ✅ Click **OK**.
* ✅ Continue to completing the line.

### No exact SKU

* ✅ Click **Cancel**.
* ✅ Continue to VAT/Product creation.

### Conflicting results

* ✅ **Stop for manual review.**

---

# J. VAT — Existing VAT Path

Before creating a missing Product:

* ✅ Keep the Order open.
* ✅ Open **Data > VATs**.
* ✅ Search exact required VAT name, e.g. `VAT 19%`.

Reuse an existing VAT only if all of the following match:

* ✅ Name = `VAT {percentage}%`
* ✅ Value = extracted VAT percentage.
* ⬜ VAT code (E-Invoice) = **S (Standard rate)** `[N4]`

### Correct exact VAT exists

* ✅ Reuse it.

### VAT exists but configuration conflicts

* ✅ **Stop for manual review.** *(Name/Value conflicts only — see `[N4]`.)*

### VAT missing

* ✅ Create it.

---

# K. VAT — Creation Path

* ✅ Click green `+`.
* ✅ Name = `VAT {percentage}%`
* ✅ Description = `VAT {percentage}%`
* ✅ VAT code (E-Invoice) = **S (Standard rate)**
* ✅ Value = extracted percentage.
* ✅ Leave displayed **Standard VAT** unchanged.
* ✅ Save once.

---

# L. Product — Creation Path

Only after the required VAT exists:

* ✅ Click **New product**.
* ✅ Set **Item Number** = extracted SKU.
* ✅ Set **Name** = extracted item description.
* ✅ Set **Description** = extracted item description.

## Product gross-price calculation

Calculate:

```text
Price (gross)
=
Unit net price × (1 + VAT percentage / 100)
```

* ✅ Round to **2 decimal places**.
* ✅ Do **not** apply the transaction-line discount to Product master price.

## Remaining Product fields

* ✅ Cost price (net) = `0.00`
* ✅ Select exact required VAT.
* ✅ Stock = `0.00`
* ✅ Leave Category blank/unchanged.
* ✅ Leave GTIN blank/unchanged.
* ✅ Leave supplier code blank/unchanged.
* ✅ Leave allowance blank/unchanged.
* ✅ Leave Product Picture blank/unchanged.
* ✅ Leave user-defined field 1 blank/unchanged.
* ✅ Save once.

---

# M. Verify Newly Created Product

* ✅ Return to the still-open Order.
* ✅ Reopen **Select a product**.
* ✅ Search the exact SKU again.

### Product appears

* ✅ Select it.
* ✅ Click **OK**.

### Product does not appear

* ✅ **Stop for manual review.**

---

# N. Complete Each Order Item Line

After Product selection:

* ✅ Set **Qty.** to extracted quantity.
* ✅ Set or confirm **U.Price** = extracted Unit net price.
* ⬜ Set or confirm **VAT** = extracted percentage. `[N5]`
* ✅ Set line **Discount** = extracted item discount.

Verify:

```text
Line Price
=
quantity
× unit net price
× (1 - discount / 100)
```

* ⬜ Confirm displayed line Price matches expected value. `[N5]`
* ✅ Repeat the entire Product process for every remaining item. `[N8]`

---

# O. Complete the Order

Before saving:

## Debtor

* ✅ Debtor matches source.
* ⬜ Invoice address matches source. `[N2]`
* ⬜ Delivery address matches source. `[N2]`

## Products

For every line:

* ✅ Correct SKU.
* ✅ Correct description/product.
* ✅ Correct quantity.
* ✅ Correct unit price.
* ⬜ Correct VAT. `[N5]`
* ✅ Correct line discount.
* ⬜ Correct line price. `[N5]`

## Order-level values

* ⬜ Overall Discount = `0%` unless source explicitly supplies a value. `[N6]`
* ⬜ Shipping = **Free of shipping costs / 0.00** unless source explicitly supplies corresponding shipping values. `[N6]`

## Totals

* ✅ Total Net matches source.
* ✅ VAT matches source.
* ✅ Total matches source.

---

# P. Save and Verify Order

* ✅ Click toolbar **Save** once.
* ✅ Open **Data > Documents**.
* ✅ Find exactly the saved Order.
* ✅ Verify generated Order number.
* ✅ Verify expected Date.
* ✅ Verify Cust.Ref.
* ✅ Verify state is **open**.
* ✅ Verify Total.

---

# Q. Create the Linked Invoice

From the **saved Order**:

* ✅ Use **Create a follow-up document**.
* ✅ Click **Invoice**.
* ✅ Do **not** use the global Invoice button in the top toolbar.
* ✅ Wait for the linked **New Invoice** editor.

---

# R. Verify Invoice Inheritance

Leave Fakturama-generated values unchanged:

* ✅ Invoice No.
* ✅ Invoice Date.
* ✅ Service date.

Confirm the Invoice copied correctly from the Order:

* ✅ Cust.Ref.
* ⬜ Invoice address. `[N7]`
* ⬜ Delivery address. `[N7]`
* ⬜ Order Date. `[N7]`
* ⬜ VAT mode. `[N7]`
* ⬜ Item lines. `[N7]`
* ✅ Totals.

---

# S. Invoice Payment Method

* ✅ Set or confirm Invoice Payment Method = extracted Payment Method.

### Required method available

* ✅ Select/confirm it.

### Required method unavailable

* ✅ **Stop for manual review.**
* ✅ Do not create a new method at this stage.

---

# T. Invoice Paid / Unpaid Branch

## Source status = PAID

* ✅ Select/check **paid**.
* ✅ Set Payment Date = extracted Payment Date.
* ✅ Set Value = full Invoice Total.

## Source status ≠ PAID

* ✅ Leave **paid** clear.
* ✅ Do not invent a Payment Date.
* ✅ Do not invent a payment Value.

---

# U. Save and Verify Invoice

* ✅ Click toolbar **Save** once.
* ✅ Open **Data > Documents**.

Verify Invoice:

* ✅ Expected state.
* ✅ Expected Total.

Verify source Order:

* ✅ Order still exists.
* ✅ Order remains **open**.
* ✅ Same Cust.Ref.
* ✅ Same Total.

---

# V. Optional Final Invoice Reopen

Only when necessary to verify persisted payment details — not implemented
(the spec marks this step optional; the Documents-list verification in §U
covers the state/total re-check instead):

* ⬜ Reopen Invoice.
* ⬜ Confirm Payment Method.
* ⬜ Confirm paid state.
* ⬜ Confirm Payment Date.
* ⬜ Confirm payment Value.

---

# W. Stop Conditions

After final Invoice verification:

* ✅ End the automation.
* ✅ Do **not** create a Delivery.
* ✅ Do **not** create a Correction.
* ✅ Do **not** create a Dunning document.

---

# X. Manual Review Paths

The automation must stop rather than guess when it encounters:

* ✅ Ambiguous/conflicting Debtor search.
* ✅ Multiple conflicting Payment Method definitions.
* ✅ Conflicting Product/SKU search.
* ✅ Conflicting VAT configuration. *(Name/Value only — see `[N4]`.)*
* ✅ Newly created Product cannot be found again.
* ✅ Newly created Debtor cannot be found again.
* ✅ Invoice requires a Payment Method that is unavailable.
* ✅ Expected order/invoice data cannot be verified.

The recurring decision pattern is:

```text
                 SEARCH
                    │
          ┌─────────┼─────────┐
          │         │         │
        EXACT     MISSING   CONFLICT
          │         │         │
          ▼         ▼         ▼
         USE      CREATE    MANUAL
                    │       REVIEW
                    ▼
               SEARCH AGAIN
                    │
                    ▼
                  VERIFY
```

*(The complete end-to-end flowchart for this process has moved to
`DESIGN.md`.)*

# Final Functional Completion Checklist

* ✅ Can one order image be supplied as input?
* ✅ Is its data extracted into structured form?
* ✅ Is a New Order opened first?
* ✅ Is the Debtor searched before being created?
* ✅ Are ambiguous Debtors rejected for manual review?
* ✅ Can a missing Debtor be created while keeping the Order open?
* ✅ Can a missing Payment Method be created when required during Debtor setup? `[N3]`
* ✅ Is every Product searched by exact SKU before creation?
* ✅ Is VAT verified/created before a missing Product is created?
* ✅ Is Product gross price calculated without applying the Order-line discount?
* ✅ Is the Order-line discount applied only to that transaction line?
* ⬜ Are all line calculations verified? *(Order-level totals only — see `[N5]`.)*
* ✅ Are final Order totals verified?
* ✅ Is the saved Order verified in Documents?
* ✅ Is the Invoice created as a **follow-up from the Order**?
* ⬜ Is Invoice inheritance verified? *(Cust.Ref and totals only — see `[N7]`.)*
* ✅ Is PAID status handled correctly?
* ✅ Is unpaid data left uninvented?
* ✅ Is the saved Invoice verified?
* ✅ Does the source Order remain open?
* ✅ Does the flow stop without creating Delivery, Correction, or Dunning documents?
