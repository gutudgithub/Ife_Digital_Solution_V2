# Ife Digital Solution — Proposed Stage 3B Feature Brief

## Status

Approved by the product owner on 12 September 2026. Implementation is in progress.

## Name

Posted-sale reversal, customer returns, refund evidence, and internal return receipts.

## Outcome

An owner or manager can correct a posted sale without editing or deleting its original
sale, inventory movements, payment, or receipt. The correction uses immutable compensating
events that restore eligible stock, record the exact selling amount returned to the
customer, and preserve cash or manually referenced Telebirr refund evidence.

Stage 3B completes the minimum controlled sale lifecycle needed before unrestricted pilot
use. It does not add customer accounts, exchanges as a special transaction type, provider
integration, cash-session accounting, statutory accounting, or official tax documents.

## Recommended decisions

### 1. Correct posted sales only through linked compensating records

Keep the Stage 3A sale, lines, payment, receipt, and outbound inventory movements immutable.
Add two correction purposes:

- `sale_reversal`: a complete correction of an incorrectly posted sale; and
- `customer_return`: a partial or complete return of goods from a real sale.

Both use one sale-return record and exact source sale lines. A full sale reversal is allowed
only when no posted, unreversed return already exists for the sale, and it must include every
sale line for its full original quantity. Once a sale has a partial customer return, further
corrections use customer returns against the remaining quantity rather than a full reversal.

The original sale remains posted. Reversed and returned quantities and amounts are derived
from linked immutable correction records rather than by changing the sale status or total.

### 2. Return only against the original sale line and branch

Every return line references one exact posted sale line. Returnable quantity is:

`original sold quantity - quantities on posted, unreversed returns`

The return must use the original sale branch. Cross-branch returns, receipt-free returns,
substituting another SKU, and returning more than the remaining sold quantity are rejected.
Inactive products and variants remain returnable because the original immutable sale line is
the source of truth.

Stage 3B accepts only goods that are physically returned and suitable for sale, so every
posted return restores stock. Damaged, missing, non-sellable, or refund-without-return cases
remain outside this slice and require a separately approved disposition workflow.

### 3. Restore inventory at the original sale-line assigned cost

Each return line copies the source sale line's assigned inventory unit cost. Posting restores
quantity through an inbound inventory movement valued at:

`returned quantity × original sale-line assigned inventory unit cost`

The inbound movement then recalculates the branch balance's moving average using the existing
Stage 2 inbound formula. It does not use the current average as the restoration cost.

This matches Stage 2B's reversal principle: a compensating inbound restores stock using the
original outbound event's assigned cost. The returned selling amount and restored inventory
value remain separate operational facts and must not be labeled profit, loss, margin, or
taxable income.

The product owner approved the matching reversal policy on 12 September 2026: reversing a
posted return removes stock at the return movement's assigned cost, not the then-current
moving average. The outbound movement therefore negates the posted return's quantity and
inventory-value deltas exactly, and the remaining balance average is recalculated from its
remaining quantity and value.

### 4. Refund exactly the original selling amount for returned quantity

The refund amount is the sum of each returned quantity multiplied by the immutable original
sale-line selling price, quantized to the money precision. It must equal the complete return
total.

Stage 3B has no restocking fee, refund deduction, partial refund, goodwill payment, price
override, refund above the original selling amount, or store credit. An exchange is recorded
as a posted customer return plus a separate new Stage 3A sale; the system does not hide the
two transactions inside an exchange edit.

### 5. Record cash or Telebirr refund evidence without provider claims

Each posted return has exactly one refund-evidence record:

- `cash`: no external reference; or
- `telebirr`: a nonblank manually entered refund transaction reference.

Preserve the entered Telebirr reference and store a normalized comparison value that is
unique within one business's refund records. Refund references use a separate outgoing
evidence namespace from Stage 3A incoming payment references.

The record means only that an authorized operator recorded a refund. It is not provider
verification, settlement confirmation, reconciliation, a chargeback, or evidence that
Telebirr accepted or completed the transaction.

### 6. Restrict refund authority

- Owner and manager: create, edit, cancel, post, and reverse sale corrections across
  authorized business branches; view selling totals, assigned costs, and inventory values.
- Cashier: prepare and view customer-return drafts only for the assigned branch; cannot post
  a refund, create a full sale reversal, reverse a posted return, or view inventory costs or
  values.
- Stock employee: no refund or sale-correction workflow.
- Platform-staff status alone: no operational access.

Posting and reversal require a fresh authorization check. A creator cannot gain authority by
submitting business, branch, actor, cost, price, or total identifiers.

### 7. Correct an erroneous posted return through one full reversal

A posted return is immutable. An owner or manager may reverse the complete return once with
a mandatory reason and idempotency key.

The reversal:

- preserves the original return and refund evidence;
- creates linked compensating outbound inventory movements;
- uses the current branch moving-average cost and the existing subtract-and-clamp outbound
  valuation at reversal time;
- records an immutable reversal of the internal refund evidence without claiming that cash
  or Telebirr was recovered from the customer; and
- makes the original quantities returnable again.

Reversal is rejected if current stock cannot cover the outbound quantity. If returned goods
have already been sold or otherwise removed, the operator must investigate and correct stock
through an approved inventory process rather than creating negative stock.

## Included

- business- and branch-scoped sale returns;
- generated internal return number;
- `sale_reversal` and `customer_return` purposes;
- `draft`, `posted`, `reversed`, and `cancelled` states;
- exact source sale and source sale-line references;
- partial and full customer returns;
- one complete sale reversal when no prior active return exists;
- remaining-returnable quantity derived from posted, unreversed return lines;
- immutable product, SKU, stock-unit, quantity, original selling-price, and original assigned
  inventory-cost snapshots;
- inbound inventory restoration at the original sale-line assigned cost;
- current moving-average recalculation through the existing inbound formula;
- exactly one refund-evidence record equal to the return selling total;
- cash and manually referenced Telebirr refund evidence;
- normalized business-unique Telebirr refund reference;
- one internal return/refund receipt linked to the original receipt;
- return-posting and return-reversal idempotency keys;
- deterministic locking and atomic multi-line posting;
- full reversal of an erroneous posted return;
- exchanges represented as return plus a separate new sale;
- owner/manager posting authority and assigned-branch cashier draft preparation;
- sale detail showing original, returned, and net quantities and selling amounts;
- filters and pagination for returns;
- tenant, branch, role, quantity, valuation, refund, idempotency, concurrency, rollback,
  immutability, accessibility, translation, and disclaimer tests.

## Explicit exclusions

- customer identity, account, contact details, consent, loyalty, or return history by person;
- receipt-free returns, cross-branch returns, substitute-SKU returns, or return-policy
  deadline enforcement;
- damaged, defective, missing, non-sellable, or refund-without-return disposition;
- restocking fees, refund deductions, partial monetary refunds, goodwill payments, price
  overrides, over-refunds, store credit, vouchers, or gift cards;
- exchange as a special mutable transaction;
- credit-sale correction, accounts receivable, deposits, layaway, split tender, or payment
  allocation;
- Telebirr API calls, provider verification, webhooks, settlement, reconciliation,
  provider-led refunds, chargebacks, or retry orchestration;
- cash-session balance, expected cash, till withdrawal, cash count, variance, banking, or
  general ledger posting;
- VAT, withholding, fiscal-device integration, statutory accounting, official tax credit
  notes, official tax invoices, or electronic invoicing;
- profit, margin, loss, taxable-income, or certified-accounting claims;
- offline posting, bulk import, inter-branch transfer, public ordering, delivery,
  professional services, payroll, or AI.

## Data boundaries

### Sale return

Stores direct business and branch scope, source sale, generated internal number, purpose,
state, return date, reason, creator, poster, reversal actor, timestamps, and posting/reversal
idempotency relationships.

### Sale return line

Stores direct business scope, source return, exact source sale line and variant, immutable
product/SKU/unit snapshots, returned quantity, original selling unit price, refund line
total, original sale-line assigned inventory unit cost, and posted inventory-value
restoration.

### Sale refund evidence

Stores direct business and branch scope, source return, method, exact amount, entered
Telebirr refund reference, normalized reference, actor, and timestamp. It is operational
evidence, not provider confirmation.

### Internal return receipt

Stores direct business and branch scope, source return and original sale receipt, generated
internal number, immutable return/refund total, actor, and issue timestamp. It states that it
is an internal transaction record and not an official tax invoice or official tax credit
note.

### Sale return reversal

Stores direct business and branch scope, source posted return, mandatory reason, actor,
timestamp, and idempotency key. Its inventory movements and refund-evidence reversal remain
immutable.

## Draft rules

1. Resolve the source sale inside the actor's active business and authorized branch.
2. Require a posted Stage 3A sale.
3. Require at least one exact source sale line and a positive quantity.
4. Reject quantities above remaining returnable quantities.
5. Require every selected line to belong to the same source sale and branch.
6. Copy immutable product, SKU, unit, selling-price, and assigned-cost evidence.
7. Derive refund line totals from source selling prices.
8. A draft has no inventory, refund, receipt, or cash-session effect.
9. A cancelled draft cannot be edited or posted.
10. A full sale-reversal draft must include every original line for its full quantity and is
    unavailable after any posted, unreversed customer return.

## Posting rules

1. Resolve the return from the actor's active business and authorized branch.
2. Require owner or manager authority.
3. Lock the return, source sale, source sale lines, prior return lines, variants, and
   inventory balances in deterministic order.
4. Recompute remaining returnable quantities under lock.
5. Require all variants, source snapshots, prices, and assigned costs to match the immutable
   source sale lines.
6. Restore each quantity at the source sale line's assigned inventory unit cost.
7. Recalculate the moving-average balance through the existing inbound formula.
8. Require exactly one cash or Telebirr refund-evidence record equal to the return total.
9. Require a normalized business-unique refund reference for Telebirr and no reference for
   cash.
10. Create return lines, inbound movements, refund evidence, internal return receipt,
    actor/timestamp evidence, and posted state in one transaction.
11. Replaying the same posting key for the same return yields the existing result without
    duplicate movements, refund evidence, or receipt.
12. Reusing a key for another operation returns a stable translated validation error.
13. Any quantity, stock, valuation, refund, reference, receipt, or final-state failure rolls
    back the complete posting.

## Return-reversal rules

1. Only an owner or manager may reverse a posted return.
2. Require a nonblank reason and caller-supplied idempotency key.
3. Lock the return, variants, and balances in deterministic order.
4. Reject a second reversal.
5. Require current branch stock and inventory value to cover every reversal quantity and
   the exact value originally restored.
6. Remove restored quantity at the return movement's assigned inventory unit cost, reversing
   exactly the quantity and inventory value added by the posted return.
7. Create linked outbound movements and immutable refund-evidence reversal records.
8. Mark the return `reversed` without deleting or editing original records.
9. Reversed return quantities become returnable again.
10. Any line failure rolls back the complete reversal.

## Permission rules

- Owner and manager: all Stage 3B actions across authorized business branches; cost/value
  visibility.
- Cashier: create, edit, cancel, and view customer-return drafts for the assigned branch;
  view posted return selling totals and refund references for that branch; no posting,
  reversal, full sale reversal, or cost/value visibility.
- Stock employee: no Stage 3B access.
- Platform staff without an active business membership: no Stage 3B access.

Cross-branch correction authority uses the explicit sales branch capability and is not
derived from cost visibility.

## Interface

- Add Returns under Sales for authorized roles.
- Return list filters by branch, purpose, state, refund method, original sale/receipt number,
  return number, Telebirr refund reference, and business-date range.
- Draft form begins from a posted sale or original receipt and shows original, previously
  returned, and remaining quantities.
- Full sale reversal has a separate manager-only confirmation path.
- Posting confirmation shows restored stock quantities, exact refund total, refund method,
  Telebirr reference when required, and the immutable-posting warning.
- Return detail links the original sale and receipt, return lines, inventory movements,
  refund evidence, and internal return receipt.
- Sale detail shows gross sold, returned, and net quantities and selling totals without
  changing the original receipt.
- Return reversal confirmation warns that stock will be removed and that provider or cash
  recovery is not being asserted.
- Internal return receipts are print-friendly and visibly state that they are not official
  tax invoices or official tax credit notes.
- All errors are field-associated where possible, translation-ready, and accessible.

## Acceptance criteria

1. A return cannot reference another business, branch, sale, sale line, or variant.
2. Cross-business URLs fail without disclosing record existence.
3. A draft return has no inventory, refund, receipt, or cash-session effect.
4. A return cannot post without at least one valid line and reason.
5. Returned quantity cannot exceed remaining sold quantity.
6. Multiple partial returns reconcile exactly to each source sale line.
7. Concurrent returns cannot over-return one sale line.
8. A full sale reversal includes every line and is unavailable after another active return.
9. Posting restores quantity exactly once.
10. Restored inventory uses the source sale line's assigned inventory unit cost.
11. The moving-average balance and movement ledger reconcile exactly after returns.
12. Refund evidence equals the returned selling amount exactly.
13. Cash rejects an external refund reference.
14. Telebirr requires a manually entered, normalized, business-unique refund reference.
15. Concurrent reuse of one refund reference cannot create duplicate refund evidence.
16. Replayed posting keys create no duplicate return, movement, refund, or receipt.
17. Cross-operation key reuse returns a stable translated validation error.
18. Multi-line, refund, receipt, or final-state failure rolls back every write.
19. Posted returns, refund evidence, receipts, reversals, and movements reject normal
    edit/delete flows.
20. A posted return can be reversed only once.
21. Return reversal cannot make stock or inventory value negative and rolls back atomically
    on failure.
22. Return reversal uses the return movement's assigned cost and exactly conserves the
    posted return's quantity and inventory-value effect.
23. Reversed quantities become returnable again.
24. Cashiers cannot post refunds, reverse returns, create full sale reversals, access other
    branches, or view inventory costs or values.
25. Stock employees and platform staff without membership cannot access Stage 3B.
26. An exchange is represented by a return and a separate new sale.
27. Return screens and receipts make no provider-confirmation, official-tax, profit, margin,
    loss, receivable, or cash-balance claim.
28. Filters and pagination preserve each other.
29. All user-facing strings are translation-ready and forms/tables are accessible.
30. Ruff, formatting, mypy, migration checks, Django checks, PostgreSQL tests, and pre-commit
    pass.

## Independent review focus

Claude should verify exact source-line traceability, remaining-returnable calculations,
full-reversal restrictions, original-assigned-cost stock restoration, moving-average
recalculation, exact refund equality, Telebirr evidence wording and concurrency,
idempotency, deterministic locking, atomic rollback, return-reversal stock safety,
immutability, tenant/branch/role boundaries, cashier cost non-disclosure, receipt
disclaimers, and excluded accounting/provider/customer claims.

## Product-owner decision requested

Approve this recommended Stage 3B scope as written, request changes, or defer it. Approval
authorizes implementation of the controlled sale-correction lifecycle only; every explicit
exclusion remains separately gated.
