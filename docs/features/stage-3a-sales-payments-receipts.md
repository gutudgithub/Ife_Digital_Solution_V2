# Ife Digital Solution — Proposed Stage 3A Feature Brief

## Status

Proposed for product-owner approval.

## Name

Fully paid branch sales, cash or Telebirr recording, and internal receipts.

## Outcome

An authorized cashier, manager, or owner can prepare and post a fully paid sale
for one branch. Posting atomically reduces stock, preserves the selling-price
and assigned inventory-cost evidence, records one cash or Telebirr payment, and
creates a clearly labeled internal receipt.

This slice proves the normal sale path before adding post-sale corrections.
Stage 3B must add controlled sale reversal, customer returns, and refunds before
the sales module is accepted for unrestricted pilot use.

## Recommended decisions

### 1. Require full payment with one tender

Each posted Stage 3A sale has exactly one payment whose amount equals the sale
total. The allowed methods are:

- cash; or
- Telebirr with a manually entered transaction reference.

Credit, partial payment, split tender, overpayment, change calculation, payment
allocation, customer balance, and accounts receivable remain deferred.

One tender keeps posting and reconciliation deterministic while honoring the
approved cash-and-Telebirr decision. The model may be extended later through an
approved migration rather than exposing unused allocation behavior now.

### 2. Treat Telebirr references as operational evidence

A Telebirr payment requires a nonblank manually entered reference. Preserve the
operator's entered reference for display and store a normalized comparison
value so accidental case or whitespace variations cannot reuse the same
reference within one business.

This is not provider verification, settlement confirmation, or reconciliation.
Telebirr API, webhook, provider-led refund, and automatic matching remain
deferred.

### 3. Use the current catalog selling price without discounts

When a variant is added to a draft, copy its current selling price to the sale
line. Cashiers cannot override prices, apply discounts, or create zero-priced
lines in Stage 3A.

At posting, reject the sale if a variant's current selling price differs from
the draft snapshot. The operator must refresh the draft and confirm the new
total; the server never silently reprices a submitted sale.

Promotions, coupons, line discounts, sale-level discounts, manager price
overrides, tax-inclusive/exclusive pricing, and negotiated prices remain later
approved features.

### 4. Assign moving-average inventory cost at posting

The sale line preserves:

- product name, SKU, stock unit, quantity, and selling unit price;
- assigned branch/variant moving-average inventory unit cost; and
- the resulting inventory-value reduction.

Posting uses Stage 2's outbound subtract-and-clamp behavior. It subtracts
quantity multiplied by assigned moving-average cost from stored inventory
value, never recomputes stored value as remaining quantity multiplied by a
rounded average, and sets average cost to zero if rounded value is exhausted.

The assigned inventory cost is operational stock valuation evidence. Stage 3A
does not label the difference between sale amount and inventory-value reduction
as gross profit, margin, taxable income, or net profit.

### 5. Keep the posted sale immutable

Sale states:

- `draft`: editable and has no stock, payment, or receipt effect;
- `posted`: immutable, fully paid, and linked to posted stock movements,
  payment evidence, and an internal receipt;
- `cancelled`: an unposted draft that cannot be posted.

Stage 3A does not delete, edit, void, return, or refund a posted sale. Stage 3B
will define compensating events and authorization for those corrections.

### 6. Label the receipt as an internal transaction record

Each posted sale creates one immutable printable receipt containing:

- business and branch;
- generated internal receipt number;
- sale date/time;
- item descriptions, quantities, unit prices, and line totals;
- sale total;
- payment method;
- Telebirr reference when applicable;
- operator; and
- an explicit statement that it is an internal transaction record, not an
  official tax invoice.

No VAT number, withholding, fiscal-device claim, official invoice number, or
electronic-invoicing claim is included.

### 7. Do not collect customer identity in Stage 3A

Anonymous counter sales are sufficient for this slice. Customer membership,
personal details, consent, loyalty, and customer-facing QR identity belong to
the approved later customer stage.

## Included

- business- and branch-scoped sale drafts;
- generated internal sale and receipt numbers;
- draft, posted, and cancelled sale states;
- one or more positive sale lines;
- immutable product-name, SKU, unit, quantity, and selling-price snapshots;
- server-controlled current catalog price;
- whole-number validation for piece, pair, and pack;
- current moving-average inventory cost assigned at posting;
- one immutable outbound inventory movement per sale line;
- negative-stock prevention;
- deterministic variant and balance locking;
- atomic sale, movement, payment, and receipt posting;
- one exact full payment per posted sale;
- cash and Telebirr payment methods;
- required normalized business-unique Telebirr reference;
- one immutable internal receipt per posted sale;
- caller-supplied business-scoped posting idempotency key;
- draft cancellation;
- branch/date/status/payment-method sale filtering and pagination;
- receipt lookup and printable server-rendered receipt;
- owner/manager/cashier role boundaries;
- cost and inventory-value non-disclosure to cashiers;
- tenant, branch, quantity, price-change, stock, idempotency, concurrency,
  rollback, immutability, accessibility, translation, and receipt-label tests.

## Explicit exclusions

- credit sales, partial payments, deposits, layaway, split tender, overpayment,
  change calculation, payment allocation, and customer balances;
- posted sale voids, sale reversals, customer returns, exchanges, refunds, and
  store credit;
- Telebirr API verification, webhooks, automatic reconciliation, provider-led
  refunds, retries, and chargeback handling;
- discounts, promotions, coupons, negotiated pricing, and cashier price
  overrides;
- customer identity, membership, loyalty, consent, and public ordering;
- VAT, withholding, statutory accounting, official tax invoices, fiscal
  devices, and electronic invoicing;
- cash-session open/close, expected cash, cash counting, variance, banking, and
  supplier settlement;
- profit, margin, income-statement, or certified accounting claims;
- inter-branch sales, transfers, offline posting, bulk import, delivery,
  professional services, payroll, and AI.

## Data boundaries

### Sale

Stores direct business and branch scope, generated internal number, state,
sale business date, creator, poster, timestamps, cancellation evidence, total
selling amount, payment status, and posting idempotency relationship.

### Sale line

Stores direct business scope, sale, variant, immutable product/SKU/unit
snapshots, quantity, selling unit-price snapshot, line selling total, assigned
inventory unit cost, and inventory-value reduction.

### Sale posting key

Stores direct business scope, caller-supplied UUID, target sale, and creation
time. A key belongs to exactly one sale posting.

### Sale payment

Stores direct business and branch scope, source sale, payment method, exact
amount, operator-entered Telebirr reference, normalized reference, actor, and
posting timestamp. It is operational evidence, not provider confirmation.

### Internal receipt

Stores direct business and branch scope, source sale, generated internal
receipt number, immutable sale/payment totals, actor, and issue timestamp.
Line detail is derived from immutable posted sale lines.

## Draft rules

1. Resolve all variants inside the actor's active business.
2. Require the actor to have sales permission for the selected branch.
3. Require at least one positive quantity.
4. Reject inactive variants for new draft lines.
5. Validate whole-number units.
6. Copy current product, SKU, unit, and selling-price snapshots.
7. Reject zero selling prices.
8. A draft has no inventory, payment, receipt, or cash-session effect.
9. A cancelled draft cannot be edited or posted.

## Posting rules

1. Resolve the sale from the actor's active business and authorized branch.
2. Lock the sale, variants, and inventory balances in deterministic order.
3. Require a valid draft with at least one line.
4. Require every variant to remain in the same business and every unit snapshot
   to match.
5. Reject posting if a catalog selling price changed after the draft snapshot.
6. Reject any quantity that would make branch stock negative.
7. Assign the current six-decimal moving-average inventory cost.
8. Apply the existing subtract-and-clamp outbound valuation.
9. Require exactly one payment equal to the final sale total.
10. Require cash or Telebirr; require a unique normalized Telebirr reference
    for Telebirr and no provider-confirmation claim.
11. Create posted sale lines, inventory movements, payment, internal receipt,
    actor/timestamp evidence, and posted state in one transaction.
12. Replaying the same idempotency key for the same sale returns the existing
    result without duplicate movements, payment, or receipt.
13. Reusing the key for another sale returns a stable translated validation
    error.
14. Any line, stock, price, payment, reference, or receipt failure rolls back
    the complete posting.

## Permission rules

- Owner and manager: create, edit, cancel, post, and view sales across
  authorized branches; view assigned inventory costs and value reductions.
- Cashier: create, edit, cancel, post, and view sales only for the assigned
  branch; view selling totals and payment references but never assigned
  inventory costs, average costs, or inventory values.
- Stock employee: no sale creation, posting, payment, or receipt access in
  Stage 3A.
- Platform staff status alone grants no operational sales access.

## Interface

- Add Sales navigation for authorized roles.
- Sale list filters by branch, status, business date, payment method, sale
  number, receipt number, and Telebirr reference.
- Draft form selects variants and quantities and displays server-controlled
  prices and totals.
- Posting confirmation displays the final total, payment method, Telebirr
  reference field when required, and a warning that posting is immutable.
- Posted detail links to its internal receipt.
- Receipt view is print-friendly and visibly labeled as an internal transaction
  record rather than an official tax invoice.
- Price changes, duplicate Telebirr references, stock shortages, and stale
  drafts return associated, translated form errors rather than server errors.

## Acceptance criteria

1. A sale cannot reference another business, branch, or variant.
2. A cashier cannot sell from an unassigned branch.
3. A draft has no inventory, payment, receipt, or cash-session effect.
4. A sale cannot post without at least one valid line.
5. Whole-number stock units reject fractional sale quantities.
6. The server uses the catalog price snapshot and rejects stale changed prices.
7. Posting cannot make stock negative.
8. Multi-line posting is atomic.
9. Concurrent sales cannot oversell one balance or lose a balance update.
10. Posting decreases quantity and value exactly once.
11. Outbound value never increases inventory value.
12. Sale lines preserve assigned moving-average inventory cost.
13. A posted sale has exactly one full payment equal to its sale total.
14. Cash posts without an external transaction reference.
15. Telebirr requires a manually entered, normalized, business-unique
    reference.
16. Concurrent reuse of one Telebirr reference cannot create two payments.
17. Replayed posting keys create no duplicate sale, movement, payment, or
    receipt.
18. Cross-sale key reuse returns a stable translated error.
19. Any payment or receipt failure rolls back sale and inventory effects.
20. Posted sales, lines, payments, and receipts reject normal edit/delete
    flows.
21. Cashiers cannot see assigned costs, average costs, inventory values, or
    other-branch sales.
22. Stock employees cannot access sale/payment/receipt workflows.
23. The receipt is clearly labeled as an internal transaction record and never
    as an official tax invoice.
24. No screen or report calls sale amount minus inventory value profit, margin,
    taxable income, or net profit.
25. Filters and pagination preserve each other.
26. All user-facing strings are translation-ready and forms/tables are
    accessible.
27. Ruff, formatting, mypy, migration checks, Django checks, PostgreSQL tests,
    and pre-commit pass.

## Stage 3B gate

Before sales are accepted for unrestricted pilot use, approve and implement a
separate Stage 3B brief covering:

- full posted-sale reversal for operator mistakes;
- partial and full customer returns against exact sale lines;
- stock restoration valuation;
- cash and Telebirr refund evidence without false provider-confirmation claims;
- refund authorization, idempotency, concurrency, and immutable compensating
  events;
- exchanges as explicit return plus replacement sale rather than hidden edits.

## Later cash/reconciliation decision gate

Before Stage 4 adds supplier settlement, approve a decision entry defining how
the difference between supplier-reference return totals and inventory-value
reductions is represented and what it explicitly is not. Stage 3A does not name
or account for that difference.

## Independent review focus

Claude should review tenant/branch permissions, catalog-price staleness,
quantity/unit validation, moving-average outbound valuation, full-payment
equality, Telebirr-reference normalization and concurrency, posting
idempotency, atomic rollback, posted immutability, receipt labeling, cashier
cost non-disclosure, and excluded accounting/customer claims.

## Proposed product-owner decision

Approve Stage 3A as written: one fully paid cash or manually referenced
Telebirr sale, immutable stock/payment/receipt evidence, no discounts or
customer identity, and no posted correction path until the separately reviewed
Stage 3B.
