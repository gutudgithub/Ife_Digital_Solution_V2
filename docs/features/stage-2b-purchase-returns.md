# Ife Digital Solution — Stage 2B Feature Brief

## Status

Approved by the product owner on 8 September 2026.

Implementation and follow-up CRUD hardening are independently approved by Claude with no
blocking findings.

## Name

Supplier purchase returns and inventory control history.

## Outcome

An owner or manager can return previously received goods to a supplier without
editing the original purchase or receipt. The return creates an immutable,
branch-scoped outbound inventory movement, cannot return more than was received
or more than is currently on hand, and can be corrected only through a linked
compensating reversal.

The business can also inspect purchase-cost history, supplier activity, and
filtered inventory movement history without treating those operational records
as certified accounting, supplier payables, tax records, or reputation scores.

## Recommended decisions

### 1. Return only against a posted receipt line

Every return line must reference the exact posted goods-receipt line that
introduced the goods. Returned quantity is derived from posted, unreversed
return lines. This prevents returning more than was received and preserves the
source purchase, supplier, unit, quantity, and cost evidence.

### 2. Separate the supplier amount from inventory valuation

Each return line stores:

- the original receipt unit cost as the supplier-facing reference amount; and
- the branch's current moving-average cost assigned when the return posts as
  the inventory-value basis.

Inventory value uses the current moving-average cost and follows Stage 2A's
subtract-don't-recompute rule. The stored value is reduced by returned quantity
times assigned inventory cost, clamped at zero, and average cost becomes zero
if rounded value is exhausted while quantity remains.

The supplier reference total and inventory-value reduction may differ. The
system must disclose that difference without calling it profit, loss, payable,
receivable, refund, or purchase-price variance. Those accounting meanings
remain deferred.

### 3. Use a controlled return lifecycle

Purchase-return states:

- `draft`: editable and has no ledger effect;
- `posted`: immutable and has posted inventory movements;
- `reversed`: immutable, with one linked compensating reversal;
- `cancelled`: an unposted draft that cannot be posted.

A posted return cannot be edited or deleted. A mistake is corrected by
reversing the complete return with a mandatory reason, then creating a
replacement return if needed. Partial reversal is deferred to avoid ambiguous
line state; reverse-and-replace preserves a simple audit trail.

### 4. Keep purchase receiving state separate

Do not overload the existing purchase status with return states. Purchase
receiving remains `approved`, `partially_received`, or `received`; return
progress is derived separately as gross received, returned, and net received
quantities.

### 5. Restrict posting authority

- Owner and manager: create, edit, cancel, post, and reverse returns; view
  quantities, supplier amounts, assigned inventory costs, and values.
- Stock employee: prepare a draft return and view return/receipt quantities and
  purchase costs for an authorized branch; cannot post, cancel after posting,
  reverse, or view inventory values.
- Cashier: no supplier-return, supplier-contact, purchase-cost, average-cost,
  inventory-value, or movement-history access.

### 6. Keep financial settlement out of Stage 2B

Supplier cash refunds, Telebirr refunds, credits, payables, settlement
allocations, ageing, and certified accounts payable remain deferred to the cash
and reconciliation stage. A supplier reference number or note is informational
only.

## Included

- business- and branch-scoped purchase returns;
- generated internal return number;
- draft, posted, reversed, and cancelled states;
- mandatory supplier, purchase, receipt-line, quantity, and reason;
- optional supplier return-document reference;
- immutable product, SKU, unit, receipt-cost, and supplier snapshots;
- current moving-average inventory cost assigned at posting;
- one inventory movement per posted return line;
- one linked compensating inbound movement per reversed return line;
- return and reversal idempotency keys unique within a business;
- partial returns and multiple returns against one receipt line;
- received, returned, and net-received progress derived from immutable lines;
- negative-stock prevention;
- deterministic locking of source lines, variants, and balances;
- atomic multi-line posting and reversal;
- inactive supplier and variant history remains visible and returnable when the
  source receipt and current stock are valid;
- purchase-cost history by variant and supplier;
- operational supplier activity summary: approved purchase count, received and
  returned reference amounts, last receipt date, and last return date;
- inventory movement filters by branch, product/variant, movement type, and
  business-date range, with pagination;
- tenant, branch, role, idempotency, concurrency, rollback, immutability,
  costing, accessibility, and translation tests.

## Explicit exclusions

- supplier payments, refunds, credits, payables, allocations, limits, or ageing;
- purchase-price variance accounting;
- VAT, withholding tax, statutory accounting, official tax documents, or
  electronic invoicing;
- sales, customer returns, customer refunds, and sales receipts;
- stock counts, blind counts, and reconciliation;
- inter-branch transfers;
- batch, lot, expiry, and serial tracking;
- supporting document images or transcription;
- bulk import/export;
- offline posting or synchronization;
- public supplier ratings or reputation scores;
- AI, customer ordering, delivery, professional services, payroll, wages,
  overtime, leave, and statutory employment administration.

## Data boundaries

### Purchase return

Stores direct business and branch scope, supplier, source purchase, internal
number, state, return date, reason, optional supplier document reference,
creator, poster, reversal actor, timestamps, and posting/reversal idempotency
keys.

### Purchase return line

Stores direct business scope, return, source goods-receipt line, variant,
immutable product/SKU/unit/supplier snapshots, returned quantity, original
receipt unit cost, supplier reference line total, assigned inventory unit cost,
and posted inventory-value reduction.

### Return reversal

Stores direct business and branch scope, source posted return, reason, actor,
timestamp, and idempotency key. It is immutable and can exist only once per
return.

## Posting rules

1. Resolve every identifier inside the actor's active business.
2. Lock the return, referenced receipt lines, variants, and balances in a
   deterministic order.
3. Require a valid draft with at least one positive line and a non-blank reason.
4. Require every source receipt line to belong to the same business, branch,
   supplier, and purchase as the return.
5. Reject duplicate source receipt lines inside one return.
6. Reject quantity above the source line's received quantity minus prior posted,
   unreversed returns.
7. Reject quantity above current branch stock.
8. Assign the current six-decimal moving-average cost.
9. Subtract rounded outbound value from stored inventory value; never recompute
   stored value as remaining quantity times rounded average.
10. Clamp value at zero and set average cost to zero when rounded value is
    exhausted.
11. Create immutable return lines, inventory movements, actor/timestamp
    evidence, and posted state in one transaction.
12. Replaying the same idempotency key returns the existing result; reusing it
    for another operation returns a stable validation error.
13. Any line failure rolls back the complete return.

## Reversal rules

1. Only an owner or manager may reverse a posted return.
2. Require a non-blank reason and a caller-supplied idempotency key.
3. Lock the source return, variants, and balances deterministically.
4. Reject a second reversal.
5. Restore each quantity through an inbound movement valued at the original
   return movement's assigned inventory cost.
6. Recalculate moving average using the existing inbound formula.
7. Store one immutable linked reversal and mark the source return `reversed`.
8. Any line failure rolls back the complete reversal.

## Interface

- Add Returns under Purchasing for authorized roles.
- Return list filters by state, supplier, branch, and date.
- Draft form selects a purchase and eligible posted receipt lines, then shows
  received, previously returned, available-to-return, and current-stock
  quantities.
- Posting confirmation discloses that stock will decrease and the record will
  become immutable.
- Return detail shows supplier reference amounts separately from inventory
  valuation.
- Reversal confirmation discloses that it restores stock through a new
  compensating event rather than deleting history.
- Purchase detail shows received, returned, and net-received progress.
- Supplier detail shows operational activity only, with no balance-due claim.
- Inventory history keeps cost/value columns hidden from unauthorized roles.
- All strings are translation-ready and all forms use associated accessible
  errors and status messages.

## Acceptance criteria

1. A return cannot reference another business, branch, supplier, purchase,
   receipt, receipt line, or variant.
2. A draft return has no inventory effect.
3. A return cannot post without at least one valid line and a reason.
4. Return quantity cannot exceed unreturned received quantity.
5. Return quantity cannot make current branch stock negative.
6. Multiple partial returns reconcile to the receipt line.
7. Posting decreases quantity and value exactly once.
8. Outbound value never increases inventory value.
9. Supplier reference totals preserve original receipt costs.
10. Inventory movements preserve the assigned moving-average cost.
11. A multi-line failure rolls back every return and inventory write.
12. Concurrent returns cannot over-return one receipt line.
13. Concurrent returns cannot lose a balance update.
14. Replayed idempotency keys create no duplicate return, reversal, or movement.
15. Reusing a key across operations returns a stable translated error.
16. Posted returns and reversals reject normal edit/delete flows.
17. A reversal restores quantity through linked compensating movements and can
    occur only once.
18. Purchase detail derives gross received, returned, and net received values
    without changing receiving state.
19. Stock employees cannot post or reverse; cashiers cannot access the feature.
20. Cross-business URLs fail without disclosing record existence.
21. Cost history and supplier activity remain operational and do not claim
    profit, loss, payable, receivable, refund, tax, or reputation meaning.
22. Pagination and filters preserve each other.
23. All user-facing strings are translation-ready and accessible.
24. Ruff, formatting, mypy, migration checks, Django checks, PostgreSQL tests,
    and pre-commit pass.

## Independent review focus

Claude should review source-receipt traceability, returnable-quantity
calculation, current-stock enforcement, current-average outbound valuation,
supplier-reference versus inventory-value disclosure, reversal valuation,
idempotency conflicts, deterministic locking, concurrency, rollback,
immutability, tenant/role boundaries, and excluded accounting claims.

## Approved product-owner decision

Implement this Stage 2B scope as written. It completes the purchase-return loop
without prematurely adding supplier settlement, certified accounting, stock
counts, transfers, or sales.
