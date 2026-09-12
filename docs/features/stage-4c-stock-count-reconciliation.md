# Stage 4C feature brief: stock count and inventory reconciliation

## Status

Recommended for product-owner approval. Not approved and not implemented.

## Objective

Give a branch a controlled way to pause inventory activity, record a complete physical
quantity count, compare it with the immutable inventory ledger, and post an auditable
quantity-and-value adjustment without rewriting prior purchases, sales, returns, or
movements.

Stage 4C completes inventory reconciliation only. It does not calculate profit, margin,
growth, shrinkage expense, tax, or general-ledger entries.

## Stage boundary

This stage follows independently approved Stages 1 through 4B.

Included:

- one complete stock-count session for one branch at a time;
- a system-quantity and moving-average-cost snapshot at count start;
- a temporary branch inventory-posting freeze while counting;
- blind physical quantity entry by authorized counters;
- manager review, recount correction, approval, cancellation, and controlled reversal;
- immutable count, approval, adjustment, and reversal evidence;
- exact inventory movements and updated derived balances;
- quantity-variance and inventory-value-adjustment reporting;
- repeatable demo data and PostgreSQL concurrency coverage.

Excluded:

- cycle counts, partial-location counts, spot counts, and perpetual-count scheduling;
- warehouses, bins, shelves, lots, batches, serial numbers, and expiry dates;
- barcode hardware integration and scanner-specific workflows;
- multi-branch transfers or counts;
- automatic classification as theft, damage, wastage, shrinkage expense, income, profit,
  loss, cost of goods sold, tax adjustment, or accounting journal;
- independent counted-cost valuation for every line;
- negative stock, backdating, offline counting, and historical imports;
- analytics graphs for profit, margin, net profit, or growth;
- AI-assisted recognition, anomaly detection, or recommendations.

## Product decisions

### 1. Name the difference explicitly

Stage 4C uses:

```text
stock count quantity variance
= approved physical quantity
- system quantity snapshot
```

A positive quantity variance means the approved physical count is greater than the system
snapshot. A negative quantity variance means it is lower.

The monetary effect is named:

```text
stock count inventory-value adjustment
= stock count quantity variance
× assigned count-adjustment unit cost
```

Neither value is automatically called shrinkage, gain, loss, income, expense, profit, cost
of goods sold, or an accounting adjustment. Those meanings require a separately approved
accounting policy.

### 2. Use the count-start moving-average cost

The recommended pilot valuation basis is the immutable moving-average unit cost captured
when the stock-count session starts.

Reasons:

- the physical count establishes quantity, not purchase cost;
- the branch is frozen, so the system quantity and valuation basis remain coherent during
  the count;
- the same cost can be stored on the count line and inventory movement;
- a reversal can exactly negate the posted count adjustment;
- it avoids silently inventing a second inventory-cost source.

If the system snapshot has zero quantity and zero inventory value but the approved physical
count is positive, the owner or manager must enter an evidenced unit cost for that line
before approval. The evidence note must identify the source, such as the latest supplier
document or a verified purchase record. This exceptional line cost is named
`approved count-adjustment unit cost`; it is not a general product-cost override.

Operators cannot enter a replacement cost when a nonzero moving-average snapshot exists.

### 3. Freeze branch inventory posting during the count

Starting a count places the branch in a stock-count freeze. While the count is open:

- sales may be drafted but cannot be posted;
- purchase receipts cannot be posted;
- purchase returns and their reversals cannot be posted;
- sale returns and their reversals cannot be posted;
- sale reversals cannot be posted;
- manual stock adjustments and opening balances cannot be posted;
- another stock-count session cannot start.

Cash, expense, attendance, catalog, supplier, and non-inventory draft work may continue.

The freeze begins only after the service locks the branch, confirms no other open count,
and captures all count-line snapshots atomically. Approval or cancellation releases it.
An abandoned count therefore cannot silently coexist with inventory movements; an owner or
manager must cancel it explicitly.

### 4. Count the complete branch scope

The pilot supports a complete branch count rather than partial or cycle counts.

At start, the system creates one count line for:

- every active business variant; and
- every inactive variant that has a nonzero branch balance.

Every line must receive an explicit physical quantity, including zero. An empty value means
uncounted and blocks review or approval. Products not represented by an existing catalog
variant are not counted; an authorized user must cancel the count, create the variant, and
start a new count.

The snapshot stores the variant identity, stock unit, system quantity, average unit cost,
inventory value, and count-start timestamp. Later catalog edits cannot alter that evidence.

### 5. Use blind quantity entry

Stock employees, managers, and owners may enter physical quantities. During entry, counters
see product-identification data and the stock unit but do not see:

- the system quantity snapshot;
- the expected variance;
- average unit cost;
- inventory value;
- calculated value adjustment.

This reduces anchoring and preserves existing cost-visibility boundaries.

Owners and managers see the comparison only in the review step after all lines are counted.
They may return the session to counting, replace entered quantities, and require a recount
before approval. Draft count edits retain actor and timestamp audit history.

### 6. Require variance explanation and approval

Only an owner or manager may approve a count.

Approval requires:

- every line counted;
- whole-number quantities for whole-number stock units;
- nonnegative quantities;
- a nonblank count method note;
- a nonblank explanation for every nonzero quantity variance;
- an evidenced exceptional unit cost for every positive variance whose snapshot cost is
  zero;
- the branch still frozen by this count;
- no changed, missing, or extra snapshot lines.

A zero-variance line creates no inventory movement. A nonzero line creates exactly one
immutable movement linked to the count line.

### 7. Post adjustments atomically and deterministically

Approval is one atomic service operation:

1. Validate the active owner/manager membership.
2. Lock the branch.
3. Lock the count session.
4. Lock the business-scoped idempotency key.
5. Lock snapshot variants in sorted identifier order.
6. Lock inventory balances in the same deterministic order.
7. Revalidate count completeness, units, costs, and the active freeze.
8. Post all required inventory movements.
9. Update derived balances.
10. Create the immutable approval record and release the freeze.

Any failure rolls back every movement, balance update, approval record, and posting key.

Positive quantity variance posts an inbound movement at the assigned count-adjustment unit
cost. Negative quantity variance posts an outbound movement at the same stored cost rather
than re-reading a later average. The freeze means this is also the current snapshot cost at
approval.

The existing exact-cost outbound path is used for negative count adjustments and
compensating reversals. It fails closed if quantity or inventory value would become
negative.

### 8. Approved counts are immutable

An approved count cannot be edited, deleted, reopened, or silently replaced. Source
transactions and prior inventory movements remain unchanged.

Corrections use:

1. a full count-adjustment reversal; and
2. a new complete stock count.

The reversal creates compensating inventory movements using each original count movement's
assigned cost:

- an original inbound count adjustment reverses through exact-cost outbound;
- an original outbound count adjustment reverses through inbound at the original assigned
  cost.

This restores the quantity and inventory value contributed by the count adjustment exactly.
It does not revalue the reversal at the current moving average.

Only an owner or manager may reverse. Reversal requires no other open count for the branch
and may fail closed if later inventory activity leaves insufficient quantity or inventory
value for an exact compensation. If exact reversal is no longer possible, the historical
count remains evidence and the operator must perform a new count; Stage 4C does not rewrite
history to force a reversal.

### 9. Server assigns dates and actors

The server assigns:

- count-start timestamp and Addis Ababa business date;
- snapshot timestamp;
- counter and edit timestamps;
- submission timestamp;
- approver and approval timestamp;
- reversal actor and timestamp.

Forms cannot submit business, snapshot quantity, variance, valuation basis, posting date,
approver, inventory movement, or reversal actor.

Backdating and historical count import remain excluded.

### 10. Preserve role and tenant boundaries

Owner and manager:

- start, review, approve, cancel, print, and reverse counts;
- see system quantity, variance, cost, and value evidence;
- return a submitted count for recount.

Stock employee:

- enter and edit blind physical quantities for an authorized branch;
- submit a completed count for manager review;
- cannot start, approve, cancel, reverse, or see cost/value evidence.

Cashier:

- no stock-count access;
- cannot post a sale while the branch is frozen and receives a generic instruction to ask a
  manager to complete or cancel the stock count;
- does not receive variance, count, or cost details.

Platform staff without an active business membership:

- no operational access.

Every query and service operation resolves business scope from the authenticated active
membership and validates branch authorization. Submitted business identifiers are never
trusted.

## Data model

Recommended models:

### `StockCountSession`

- business;
- branch;
- status: `counting`, `submitted`, `approved`, `cancelled`;
- idempotency key used to start;
- started by and started at;
- business date;
- count method note;
- submitted by and submitted at;
- cancellation actor, timestamp, and reason;
- immutable approval or reversal relations.

At most one `counting` or `submitted` session exists per branch.

### `StockCountLine`

- business;
- branch;
- count session;
- variant;
- product, variant-label, SKU, and stock-unit snapshots;
- system quantity snapshot;
- average-unit-cost snapshot;
- inventory-value snapshot;
- physical quantity;
- variance quantity derived at approval;
- variance explanation;
- assigned count-adjustment unit cost;
- exceptional-cost evidence note when required;
- latest counter and count timestamp.

One line exists per count session and variant.

### `StockCountLineRevision`

- business;
- branch;
- count session and line;
- immutable sequence;
- previous and replacement physical quantities;
- actor;
- reason;
- timestamp.

This preserves draft/recount edits without treating them as inventory movements.

### `StockCountApproval`

- business;
- branch;
- count session;
- approver;
- approved at;
- line count;
- positive-, negative-, and zero-variance line counts;
- total signed inventory-value adjustment;
- immutable evidence checksum or equivalent stable summary.

Quantity summaries are grouped by stock unit. The system never adds unlike units such as
pairs, pieces, and grams into one quantity total.

### `StockCountReversal`

- business;
- branch;
- approved count;
- actor;
- reason;
- reversed at;
- business-scoped idempotency key.

### `StockCountPostingKey`

- business;
- operation type;
- idempotency key;
- source reference;
- created timestamp.

The key namespace prevents reuse across count start, approval, and reversal operations.

## Inventory movement integration

Add movement types:

- `stock_count_adjustment_in`;
- `stock_count_adjustment_out`;
- `stock_count_reversal_in`;
- `stock_count_reversal_out`.

Add source types:

- `stock_count_line`;
- `stock_count_reversal`.

Movement source uniqueness remains business-scoped and variant-specific. Every nonzero
approved line has one source-linked movement. Every reversal movement links to the original
count movement or line and cannot be duplicated.

Inventory balances remain derived state. The immutable movement ledger is authoritative.

## Service operations

Required atomic services:

- start a full-branch stock count and capture snapshots;
- record or replace a blind physical count with revision evidence;
- submit a complete count for review;
- return a submitted count for recount;
- cancel an unapproved count;
- approve and post count adjustments;
- reverse an approved count adjustment exactly;
- derive review totals without accepting client-calculated values;
- check a branch inventory-posting freeze from every existing inventory posting path.

All services use explicit permissions, stable translated errors, deterministic locks,
business-scoped idempotency, and rollback on failure.

Every inventory-posting service must lock the branch and check the freeze before taking its
existing variant and balance locks. Count start takes the same branch lock before capturing
snapshots. This shared first lock makes a posting race resolve entirely before the snapshot
or reject after the freeze; it cannot commit between snapshot capture and freeze activation.

## User interface

Required server-rendered pages:

- count-session list with branch, date, status, starter, submitter, and approver filters;
- start-count confirmation explaining the inventory freeze;
- blind count worksheet grouped by product with search and manageable pagination;
- count progress showing counted and remaining line totals without expected quantities;
- submit-for-review confirmation;
- manager variance review with quantity and value summaries;
- return-for-recount form;
- cancellation form;
- approval confirmation;
- approved-count detail and print evidence;
- reversal form and reversal evidence;
- links from count movements to authorized count evidence.

The count worksheet must preserve accessibility:

- explicit labels and stock units;
- field-level and form-level errors;
- invalid-state markup;
- keyboard-usable navigation;
- no color-only variance meaning;
- translated user-facing strings.

Print evidence is internal operational evidence, not an official tax document, accounting
journal, audited stock certificate, or proof of theft or loss.

## Reporting

Stage 4C adds operational reports only:

- count history by branch and status;
- approved count summary;
- line-level system quantity, physical quantity, and quantity variance;
- quantity-variance summaries grouped by stock unit;
- signed inventory-value adjustment using stored assigned cost;
- actor and timestamp trail;
- movement links;
- reversal status.

Totals preserve six-decimal inventory value evidence. Display rounding must not replace
stored values.

Stage 5 must separately define any use of these adjustments in cost, margin, net profit,
growth, or time-series graphs.

## Acceptance scenarios

1. An owner starts a complete branch count and all inventory posting becomes blocked.
2. Start captures every active variant and every inactive variant with nonzero stock.
3. A stock employee sees a blind worksheet without system quantity or cost.
4. Empty, negative, and invalid fractional quantities return friendly field errors.
5. Explicit zero is accepted as a completed count.
6. A submitted count cannot be changed until a manager returns it for recount.
7. Every draft quantity replacement creates immutable revision evidence.
8. A nonzero variance requires a line explanation.
9. A positive variance with zero snapshot cost requires owner/manager cost evidence.
10. A nonzero snapshot cost cannot be replaced from the form.
11. Approval posts no movement for a zero-variance line.
12. Positive variance posts one inbound movement at the stored count-start cost.
13. Negative variance posts one exact-cost outbound movement.
14. Approval updates quantity and value atomically across every line.
15. A failed line rolls back every count movement and balance update.
16. Exact replay returns the same approved result without duplicate movements.
17. Concurrent approval attempts create one approval.
18. Concurrent start attempts create one open count.
19. Inventory posting racing count start resolves to either a pre-snapshot movement or a
    frozen rejection, never an unsnapshotted accepted movement.
20. Sale, receipt, return, reversal, and manual-adjustment posting all honor the freeze.
21. Cancellation releases the freeze and creates no inventory movement.
22. An approved count is immutable through normal save, delete, and admin flows.
23. Reversal negates each original count movement's quantity and value at assigned cost.
24. Reversal fails closed when later activity leaves insufficient quantity or value.
25. A new full count can reconcile the branch when exact reversal is unavailable.
26. Stock employee, cashier, and platform-staff permissions match the brief.
27. Tenant and branch isolation hold in services, views, forms, reports, and print output.
28. Cashier freeze errors disclose no quantity, variance, cost, or count details.
29. Filters and pagination preserve query parameters.
30. Demo data can create and approve the same example count repeatedly without duplicates.
31. PostgreSQL threaded tests cover count-start/posting, approval, and reversal races.
32. Migration drift, Django checks, Ruff, formatting, typing, and pre-commit checks pass.

## Suggested demo scenario

For one clothing-and-footwear branch:

- start with a T-shirt size/color variant at `10` units and a shoe-size variant at `6`
  pairs;
- start a full count as the owner;
- enter `9` T-shirts and `7` shoe pairs through a blind stock-employee worksheet;
- submit and review variances of `-1` and `+1`;
- record concise reconciliation explanations;
- approve using the captured moving-average costs;
- verify two immutable movements and reconciled balances;
- print the internal count evidence;
- demonstrate that a sale cannot post during a second open count;
- cancel that second count and verify inventory posting resumes.

## Exit gate

Stage 4C is complete only when:

- the product owner approves this brief or an explicitly revised version;
- PostgreSQL and SQLite suites pass;
- concurrency and rollback tests pass on PostgreSQL;
- migrations, checks, formatting, Ruff, mypy, pre-commit, and deployment checks pass;
- demo seeding remains repeatable;
- documentation records the approved variance name and valuation basis;
- Claude independently reviews the package and reports no blocking findings;
- the product owner accepts the reviewed stage.

Approval of this brief does not authorize Stage 5 costing, profit, margin, growth, or
time-series analytics.

## Product-owner approval and implementation status

The product owner approved this recommended Stage 4C scope as written. The implementation
adds full-branch snapshots, blind count entry, branch-level inventory freezes, review and
recount controls, exact-cost approval and reversal movements, internal print evidence,
tenant-scoped server-rendered screens, immutable admin evidence, repeatable demo data, and
PostgreSQL threaded locking coverage.

## Independent review record

Claude independently approved Stage 4C after reviewing the full-branch snapshot, blind-count
boundary, freeze coverage, count-start valuation, exact-cost adjustments and reversals,
immutable evidence, role isolation, and PostgreSQL concurrency behavior. All 252 PostgreSQL
tests and all required quality checks passed, and no Stage 1 through Stage 4C findings remain
outstanding.

Stage 5 performance intelligence remains separately gated. An accountant must approve the
definitions and treatment of revenue, inventory cost, returns, expenses, cash variance,
supplier-reference differences, and stock-count adjustments before profit, margin, net
profit, growth, or time-series graphs are implemented.

Native-speaker review remains required for translated user-facing strings before pilot
release.
