# Stage 2 feature brief: suppliers, purchasing, and inventory ledger

## Status

Approved for implementation on 11 September 2026.

## Outcome

An authorized employee can create a supplier and purchase, approve it, receive all or part of
it, and see branch stock increase through an immutable inventory movement. Owners and managers
can also establish opening stock and make reasoned stock adjustments. Branch stock quantity
and moving weighted-average cost remain reproducible from posted movements.

Stage 2 will be delivered as two reviewed vertical slices. Stage 2A establishes suppliers,
purchases, receiving, opening balances, adjustments, balances, and the movement ledger.
Stage 2B will add purchase returns and the remaining supplier/inventory controls after the
core ledger has passed independent review.

## Approved decisions used

- D2: perpetual moving weighted-average cost per product variant and branch;
- D10: validate the complete core ledger in one pilot branch while retaining branch scope;
- D12: operational documents are internal records, not official tax invoices;
- D13: all interfaces are translation-ready, with production translations deferred for
  native-speaker review.

## Proposed Stage 2A decisions

1. Use fixed common stock units: piece, pair, pack, kilogram, gram, litre, millilitre, and
   metre. Quantities use three decimal places; piece, pair, and pack require whole numbers.
   Custom unit definitions remain deferred until pilot evidence shows they are needed.
2. Do not create supplier payables or supplier-payment accounting in Stage 2A. Preserve the
   supplier reference, purchase amount, and stated settlement terms, then add auditable
   supplier payments with the cash/reconciliation ledger. This avoids implying certified
   accounts payable before that ledger exists.
3. Do not calculate VAT or represent purchase records as official supplier tax records.
   Optional supplier document references are informational only.
4. Deliver Stage 2A first. Define the purchase-return inventory-value rule in the Stage 2B
   brief before implementing returns.

## Included in Stage 2A

- business-scoped supplier directory with active/inactive status and optional contact data;
- business- and branch-scoped purchases with generated internal numbers;
- draft, approved, partially received, received, and cancelled purchase states;
- purchase lines with product/variant snapshots, unit, ordered quantity, unit cost, and line
  total;
- approval only for a non-empty valid draft;
- partial and complete receiving against approved purchases;
- immutable goods-receipt and inventory-movement history through normal application/admin
  flows;
- one stock balance projection per business, branch, and product variant;
- optional non-negative low-stock threshold per product variant;
- opening balances allowed only before the first movement for that branch and variant;
- authorized positive and negative adjustments with a mandatory reason;
- negative-stock prevention;
- caller-supplied idempotency keys for every posting operation;
- moving weighted-average cost updates for opening, receiving, and positive adjustments;
- current-average cost assignment for negative adjustments;
- tenant, branch, permission, idempotency, concurrency, validation, and rollback tests;
- translation-ready server-rendered supplier, purchase, receiving, and inventory interfaces.

## Excluded from Stage 2A

- supplier payments, certified accounts payable, credit limits, and ageing;
- purchase returns, supplier refunds, and purchase-price variance;
- VAT calculation, statutory accounting, official tax records, and electronic invoicing;
- sales, customer returns, customer refunds, and internal sales receipts;
- inter-branch transfers under D10;
- stock counts and reconciliation;
- batch, lot, expiry, and serial-number tracking;
- document-image capture and human transcription;
- bulk import/export;
- offline posting or synchronization;
- custom user-defined units;
- formal profit or inventory-valuation claims before accountant approval.

## Roles and authorization

- Owners may create and maintain suppliers, create and approve purchases, receive stock, post
  opening balances, post adjustments, and view quantities and costs.
- Managers have the same Stage 2A operational capabilities inside the active business.
- Add a stock-employee role that may view suppliers and approved purchases, receive stock,
  and view branch quantities and purchase costs. It may not approve purchases or post opening
  balances or adjustments.
- Cashiers may view active product stock quantities for the active business but may not view
  supplier contacts, purchase costs, average costs, or inventory values. Their quantity view
  is limited to their assigned branch, or the only active branch when they are unassigned.
- Every supplier, purchase, branch, variant, receipt, adjustment, and movement identifier is
  resolved inside the active tenant.

## Data model

### Product variant

Add a stock unit and optional non-negative low-stock threshold. The existing optional catalog
cost becomes a reference/default purchase cost only. Posted branch inventory movements and
balances are authoritative for cost.

### Supplier

Each supplier carries a UUID, direct business scope, name, optional phone/email/address and
notes, active status, and timestamps. Supplier name is unique inside one business.

### Purchase and purchase line

Each purchase carries direct business and branch scope, supplier, generated internal number,
optional supplier reference, operational and expected dates, state, settlement-terms note,
creator/approver identity, approval timestamp, and timestamps.

Each line carries direct business scope, purchase, product variant, immutable product/SKU/unit
snapshots, ordered quantity, unit cost, and line total. Received progress is derived from
immutable goods-receipt lines rather than stored on the purchase line, preventing projection
drift. Approved purchase lines cannot be edited through normal flows.

### Goods receipt and receipt line

Each posted receipt carries direct business and branch scope, purchase, generated internal
number, idempotency key, receiving actor, optional supplier document reference, and posting
timestamp. Lines reference purchase lines and preserve received quantity, unit, unit cost,
and value.

### Inventory movement

Each movement carries direct business and branch scope, product variant, movement type,
positive or negative quantity delta, assigned unit cost, value delta, source type and UUID,
actor, reason where applicable, and posting timestamp.

Movements reject normal updates and deletes after creation. Bulk ORM writes and raw SQL remain
outside application-level guards and are prohibited for operational application code by
`AGENTS.md`; database triggers and table privileges remain a production architecture task.

### Stock operation

Each opening balance or authorized adjustment has an immutable operation header carrying
direct business and branch scope, operation type, unique business-scoped idempotency key,
actor, mandatory reason for adjustments, and posting timestamp. Its movement references this
header. Goods receipts carry their own idempotency key and are the source for purchase-receipt
movements.

### Inventory balance

Each balance carries direct business and branch scope, product variant, quantity on hand,
moving average unit cost, inventory value, and update timestamp. A database uniqueness
constraint permits one projection per business, branch, and variant. The movement ledger is
authoritative; the balance is a concurrency-controlled projection that can be verified by a
rebuild test.

## Posting rules

### Approve purchase

1. Resolve and lock the draft purchase inside the active business.
2. Require owner/manager permission and at least one valid positive-quantity line.
3. Preserve line product, SKU, unit, quantity, and cost snapshots.
4. Set approved state, actor, and timestamp atomically.

### Receive purchase

1. Resolve and lock the approved or partially received purchase and submitted lines.
2. Validate branch, supplier, variant, unit, positive quantity, and remaining quantity.
3. Return the prior result when the same idempotency key is replayed.
4. Lock each affected inventory balance in a deterministic order.
5. Create one immutable receipt, receipt lines, and purchase-receipt movements.
6. Update quantity, value, and moving average atomically.
7. Derive received progress from receipt lines and update purchase state.
8. Roll back every effect if any line fails.

### Opening balance

1. Require owner/manager permission, branch, variant, positive quantity, unit cost, and
   idempotency key.
2. Reject opening stock when any movement already exists for the branch and variant.
3. Create the opening movement and balance atomically.

### Inventory adjustment

1. Require owner/manager permission, branch, variant, direction, positive quantity,
   idempotency key, and non-blank reason.
2. A positive adjustment requires an explicit unit cost and updates the weighted average.
3. A negative adjustment uses the current moving-average cost.
4. Reject a negative result and roll back every effect.

## Costing rules

- Quantities use `Decimal`, never floating point.
- Unit costs and moving averages preserve six decimal places; displayed money uses ETB with
  two decimal places.
- Inbound weighted average is:

  `(current value + inbound quantity × inbound unit cost) / new quantity`

- Full quantity depletion stores zero value and zero average cost. If rounded outbound cost
  consumes the remaining stored value before quantity reaches zero, value clamps to zero and
  the remaining quantity carries zero average cost until the next inbound recalculates it.
- No costing method may change after posted movements without a separately approved,
  auditable migration/recalculation procedure.

## Interface

- Add Suppliers, Purchases, and Inventory navigation for authorized roles.
- Supplier pages support list, create, detail, edit, and deactivate workflows.
- Purchase pages support list/filter, create/edit draft lines, approve, cancel when allowed,
  view receipt history, and receive remaining quantities.
- Inventory shows product, SKU, unit, branch quantity, and low/out-of-stock state.
- Authorized cost viewers also see average unit cost and inventory value.
- Opening and adjustment forms disclose that posting creates immutable movement history.
- Forms render associated accessible errors and all source strings use stable translation
  units.

## Acceptance criteria

1. A supplier and purchase cannot reference another business.
2. A purchase branch and every line variant belong to the active business.
3. An empty or invalid draft cannot be approved.
4. Approved lines cannot be silently changed.
5. Stock changes only when an opening, receipt, or adjustment posts.
6. Partial receipts cannot exceed the remaining purchase quantity.
7. Replaying an idempotency key creates no duplicate receipt or movement.
8. Concurrent receipts cannot over-receive a purchase line or lose a balance update.
9. Negative adjustments cannot make stock negative.
10. Moving weighted-average quantity, value, and cost reconcile after multiple inbound and
    outbound operations.
11. A failure on any submitted line rolls back the entire posting operation.
12. A stock employee can receive an approved purchase but cannot approve or adjust stock.
13. A cashier cannot access supplier/purchase cost or stock-value information.
14. Cross-business URLs and submitted identifiers return not-found or permission-denied
    responses without data disclosure.
15. Posted receipts and movements reject normal edit/delete flows.
16. All user-facing strings are translation-ready.
17. Ruff, formatting, mypy, migration checks, Django checks, PostgreSQL tests, and pre-commit
    pass.

## Independent review focus

Claude should review tenant and branch isolation, role boundaries, purchase-state transitions,
immutable snapshots, idempotency, deterministic locking, concurrent partial receipt safety,
negative-stock prevention, weighted-average arithmetic, projection reconciliation, rollback
behavior, migration safety, translation readiness, and the excluded accounting/tax claims.
