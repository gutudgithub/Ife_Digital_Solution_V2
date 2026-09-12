# Ife Digital Solution — Proposed Stage 4A Feature Brief

## Status

Proposed on 12 September 2026. Product-owner decision required before implementation.

## Name

Branch cash sessions, physical-cash movements, counting, and variance reconciliation.

## Outcome

A branch can open one shared cash session for its business date, post cash sales and cash
refunds into that session, record authorized physical cash added to or removed from the
drawer, count the drawer, and close with an explicit expected-versus-actual variance.

The session is an internal operational cash-control record. It is not a bank statement,
general ledger, income statement, tax record, certified reconciliation, or claim of profit.

Stage 4A is the first independently reviewable slice of roadmap Stage 4:

- Stage 4A: cash sessions and physical-cash reconciliation;
- Stage 4B: operating expenses and separately approved supplier settlement evidence; and
- Stage 4C: stock count and inventory reconciliation.

This order establishes an immutable physical-cash ledger before expenses and broader
reconciliation add more movement types.

## Recommended decisions

### 1. Use one shared branch cash session per business date

The pilot has one cash session for each branch and Addis Ababa business date. It represents
the branch's shared physical cash drawer rather than an individual cashier, terminal, till,
or shift.

Only one session may be open for a branch at a time, and only one session record may exist
for a branch and business date. A session opened before midnight may close after midnight,
but another branch session cannot open until it closes.

Opening uses the current Addis Ababa local date. Backdated and future-dated opening is not
allowed in this online-only slice.

Multiple drawers, cashier-specific tills, shift handovers, and simultaneous till sessions
remain separately gated. This avoids pretending the current pilot can distinguish physical
cash held by different drawers.

### 2. Treat opening float as physical cash, not revenue

Opening requires:

- the active business and authorized branch;
- the local business date;
- an opening float greater than or equal to zero;
- the opening actor and timestamp; and
- a caller-supplied idempotency key.

Opening creates both the session and one immutable opening-float cash movement. Opening float
contributes to expected drawer cash but is not a sale, income, profit, capital, deposit, or
accounting entry.

### 3. Require an open session only for physical-cash transactions

After Stage 4A deployment:

- posting a cash sale requires and locks the branch's open cash session;
- posting a cash customer return or full sale reversal requires and locks the branch's open
  cash session for the physical refund;
- Telebirr sales and Telebirr refunds do not change physical drawer cash and do not require
  a cash session; and
- draft, cancelled, failed, and rolled-back transactions have no cash-session effect.

The cash movement is created in the same database transaction as its sale payment or refund
evidence. If either side fails, both roll back.

Existing cash sales and refunds posted before Stage 4A are retained as historical evidence
but are not assigned to synthetic sessions or included in a later session balance. The
system must not invent an opening time, drawer, or physical count for historical records.

### 4. Record physical cash through immutable signed movements

Each cash movement belongs explicitly to one business, branch, and session and stores:

- movement type;
- signed amount delta;
- source type and immutable source identifier when system-generated;
- actor;
- reason where required; and
- posted timestamp.

Stage 4A movement types are:

- opening float: positive;
- cash sale: positive and linked to one posted sale payment;
- cash refund: negative and linked to one posted refund-evidence record;
- authorized cash added: positive; and
- authorized cash removed: negative.

Cash sale and refund sources may create at most one cash movement. Manual cash added or
removed requires an owner or manager, a positive entered amount, a nonblank reason, and a
caller-supplied idempotency key.

No negative movement may make the session's derived expected cash negative. If a recorded
refund or cash removal exceeds expected cash, the operation fails atomically. An owner or
manager must investigate and, when physically justified, record cash added before retrying.

Manual cash movements describe only physical drawer changes. They must not be labeled
expense, supplier payment, bank deposit, owner contribution, loan, income, profit, loss, or
tax without a later approved workflow supplying that meaning.

### 5. Do not pretend a return reversal recovered cash

Posting a cash return records a physical cash-refund outflow. Reversing the internal return
record does not automatically create a cash inflow because Stage 3B explicitly does not
claim that cash was recovered from the customer.

If physical cash is actually recovered, an owner or manager may record an authorized
cash-added movement with a reason. A future approved workflow may add a more specific source
type. Telebirr return reversal likewise makes no provider-settlement claim.

### 6. Calculate expected cash from immutable movements

Expected drawer cash is:

```text
opening float
+ posted cash sales
- posted cash refunds
+ authorized cash added
- authorized cash removed
```

The service derives this value from immutable session movements. It is never typed by the
operator and never silently replaced with the physical count.

All amounts use `Decimal` at ETB money precision. Movement signs and source types are
validated at both service and database boundaries.

### 7. Close with one physical count and an explicit variance

Closing requires:

- the open session;
- one nonnegative physical cash-count total;
- a caller-supplied idempotency key; and
- the closing actor and timestamp.

The close transaction locks the session, prevents new movements from racing past the close,
derives expected cash, and stores immutable snapshots:

```text
variance = actual physical cash - expected drawer cash
```

A nonzero variance requires a nonblank explanation. Zero variance may have an optional note.
The system never changes movements or transaction evidence to force the variance to zero.

Denomination counts, counterfeit handling, foreign currencies, cheque handling, bank
reconciliation, and safe/vault balances remain outside Stage 4A.

### 8. Reopen through an elevated immutable event

Only an owner or manager may reopen a closed session. Reopening requires:

- a nonblank reason;
- a caller-supplied idempotency key;
- a link to the closure being reopened; and
- actor and timestamp evidence.

The prior closure remains immutable. Reopening creates a separate event and returns the
session to open status. A later close creates a new immutable closure attempt with a new
expected amount, count, variance, and explanation.

A session may be reopened only when it is the branch's latest session and no later
branch-date session exists. This prevents reopening historical drawers beneath newer
business-day evidence.

A cashier cannot reopen, alter, or delete a closure. Normal application and admin flows
cannot edit or delete posted cash movements, closures, or reopening events.

### 9. Keep cash control separate from expenses, settlement, and profit

Stage 4A does not add:

- operating-expense categories or expense approval;
- supplier invoices, payments, refunds, credits, payable balances, or settlement;
- banking, deposits, withdrawals, bank accounts, or bank reconciliation;
- accounts receivable, credit sales, partial payment, split tender, or payment allocation;
- general-ledger accounts, journals, trial balance, income statement, or balance sheet;
- profit, gross-profit, margin, net-profit, taxable-income, or growth calculations; or
- official tax, VAT, withholding, fiscal-device, or electronic-invoicing records.

Before Stage 4B adds supplier settlement evidence, the product owner must approve what the
difference between supplier-reference return totals and inventory-value reductions means
and what it explicitly does not mean.

The owner's requested time-series graphs for growth, margin, and net profit remain targeted
for Stage 5 performance intelligence, after authoritative revenue, cost, expense, refund,
time-window, and accounting formulas receive separate approval.

## Roles and permissions

- Owner:
  - view all business branch sessions;
  - open and close sessions;
  - post manual cash added/removed movements;
  - reopen closed sessions; and
  - view expected, actual, and variance amounts.
- Manager:
  - the same Stage 4A operational capabilities as the owner across authorized branches.
- Cashier:
  - view, open, and close the assigned branch's session;
  - post normal assigned-branch cash sales;
  - view session movements without inventory cost/value;
  - enter the physical count and required variance explanation; and
  - cannot post manual cash movements or reopen a closed session.
- Stock employee:
  - no Stage 4A access.
- Platform staff without an active business membership:
  - no Stage 4A operational access.

Opening, movement posting, closing, and reopening perform fresh authorization checks.
Submitted business, branch, session, actor, expected-cash, variance, or source identifiers
must never grant authority.

## Proposed data model

### `CashSession`

- UUID primary key;
- explicit business and branch;
- Addis Ababa business date;
- `open` or `closed` status;
- opening-float amount;
- opened-by membership and opened-at timestamp;
- creation and update timestamps;
- unique business/branch/business-date constraint; and
- conditional unique open-session constraint per business/branch.

The session's lifecycle status changes only through the service layer. Opening evidence,
movements, closures, and reopening events remain immutable.

### `CashMovement`

- UUID primary key;
- explicit business, branch, and cash session;
- movement type and signed ETB amount delta;
- source type and nullable source UUID;
- actor, reason, and posted timestamp;
- unique source constraint for generated sale-payment and refund movements; and
- sign/source database constraints.

### `CashSessionClosure`

- UUID primary key;
- explicit business, branch, and cash session;
- monotonically increasing closure sequence;
- expected-cash snapshot;
- actual physical-cash count;
- variance snapshot;
- explanation;
- closed-by membership and posted timestamp; and
- posting key.

### `CashSessionReopening`

- UUID primary key;
- explicit business, branch, session, and reopened closure;
- reason;
- reopened-by membership and posted timestamp; and
- posting key.

### `CashPostingKey`

- UUID primary key;
- explicit business;
- caller-supplied UUID key;
- operation type;
- source UUID; and
- creation timestamp.

Keys are unique within a business and cannot be reused across open, manual movement, close,
or reopen operations.

## Service boundaries

All Stage 4A writes use atomic services.

### Open

1. Resolve the active membership and branch authority.
2. Validate local date and nonnegative opening float.
3. Claim the business-wide idempotency key.
4. Lock the branch/session key space.
5. Reject an existing session for the branch/date or another open branch session.
6. Create the session and opening-float movement together.

### Sale or refund integration

1. Continue the existing sale/return lock order.
2. For cash only, lock the branch's open session.
3. Reject posting if no session is open.
4. Create the source payment/refund and linked cash movement in the same transaction.
5. Preserve existing sale/return idempotency and rollback behavior.

### Manual movement

1. Require owner/manager authority.
2. Lock the open session.
3. Validate amount, direction, reason, and idempotency key.
4. Create one immutable movement.
5. Reject movement posting after closure.

### Close

1. Lock the open session.
2. Claim the idempotency key.
3. Derive expected cash from locked session movements.
4. Validate the nonnegative physical count and variance explanation.
5. Create an immutable closure snapshot.
6. Mark the session closed.
7. Roll back every change on failure.

### Reopen

1. Require owner/manager authority and lock the closed session.
2. Claim the idempotency key.
3. Validate the latest closure and mandatory reason.
4. Create an immutable reopening event.
5. Mark the session open.
6. Reject stale, duplicate, or second concurrent reopening attempts.

## Server-rendered workflow

- Add a branch cash-session status summary to the authenticated dashboard.
- Add a paginated session list with branch, date, status, opener, closer, and variance
  filters visible according to role.
- Add open, detail, manual-movement, close, and reopen screens.
- Show expected cash as read-only and calculate variance server-side.
- Show movement source labels linking to accessible internal sale/return evidence.
- Preserve filters and pagination through navigation.
- Provide a print-friendly internal cash-session close report with a disclaimer that it is
  not a bank, accounting, tax, profit, or certified reconciliation report.
- Never expose inventory assigned cost or inventory value to cashiers.
- Keep errors attached to accessible form fields and avoid leaking database constraint
  names.

## Acceptance criteria

1. Only an active membership may access Stage 4A.
2. Every session, movement, closure, reopening, and posting key has explicit business scope.
3. Every operational record retains branch scope.
4. One branch/date has at most one cash session and one branch has at most one open session.
5. Opening uses the current Addis Ababa date; future and backdated opening is rejected.
6. Concurrent open attempts produce one session and one stable translated rejection.
7. Opening float is nonnegative, immutable, idempotent, and represented by one positive
   opening movement.
8. Draft or failed sales and returns have no cash movement.
9. A posted cash sale requires an open session and creates one equal positive movement.
10. A posted cash refund requires an open session and creates one equal negative movement.
11. Telebirr sales and refunds create no physical-cash movement.
12. Sale/refund and cash movement posting are one atomic transaction.
13. Replaying sale or return posting creates no duplicate cash movement.
14. A return reversal creates no automatic cash inflow or provider-settlement claim.
15. Manual cash added/removed requires owner/manager authority, positive amount, reason, and
    idempotency key.
16. Negative movements cannot make expected cash negative and roll back atomically.
17. Cashiers cannot post manual movements, reopen sessions, or access another branch.
18. Expected cash exactly equals the immutable signed movement sum.
19. Expected cash is read-only and cannot be submitted as authority.
20. Actual cash is nonnegative.
21. Variance exactly equals actual minus expected.
22. Nonzero variance requires an explanation.
23. Closing serializes against concurrent movement posting.
24. Same-session/same-key close replay returns the original closure without duplication.
25. Cross-source or cross-operation key reuse produces a stable translated rejection.
26. Closed sessions reject new sale, refund, or manual cash movements.
27. Only owner/manager may reopen, and every reopen preserves the prior closure.
28. Only the latest branch session may reopen; a later session blocks historical reopening.
29. Reclosing creates a new immutable closure sequence rather than editing history.
30. Posted movements, closures, reopening events, and posting keys reject normal edits and
    deletes, including through admin.
31. Cashiers see operational cash amounts but no inventory cost/value or profit/margin
    claims.
32. Stock employees and platform staff without membership have no Stage 4A access.
33. Existing pre-Stage-4A cash evidence remains intact and is not assigned to synthetic
    sessions.
34. Lists are filtered, paginated, tenant-scoped, branch-scoped, and accessible.
35. Demo data creates cash controls through service APIs and remains idempotent.
36. PostgreSQL concurrency tests cover duplicate opening, movement-versus-close ordering,
    duplicate source movement, duplicate close, and duplicate reopen.
37. PostgreSQL and SQLite suites, Ruff, format, mypy, pre-commit, migrations, Django checks,
    deployment checks, and `git diff --check` pass.

## Explicit exclusions

- expense categorization, approval, attachment, reimbursement, and recurring expenses;
- supplier payment, refund, credit, payable, settlement, and purchase-price variance;
- cash denominations, shifts, multiple drawers, cashier handovers, safe/vault balances;
- bank accounts, deposits, statements, reconciliation, and payment-provider settlement;
- credit, partial payment, split tender, deposits, layaway, accounts receivable, and payment
  allocation;
- customer identity, accounts, loyalty, consent, and public ordering;
- damaged/non-sellable disposition and refund-without-return;
- inter-branch transfers and consolidated multi-branch cash;
- general ledger, official accounting statements, VAT, withholding, tax filing, official tax
  invoices/credit notes, fiscal devices, and electronic invoicing;
- profit, margin, net-profit, growth, taxable-income, or certified-reconciliation claims;
- AI, delivery, professional services, payroll, offline posting, and bulk import.

## Verification plan

- Model and constraint tests for tenant, branch, session, source, sign, lifecycle, and
  immutability invariants.
- Service tests for open, manual movement, close, reopen, idempotency, authorization,
  rollback, and historical-data boundaries.
- Sale and return integration tests for cash-session requirements and atomic cash movements.
- PostgreSQL threaded tests for open/open, movement/close, duplicate source, close/close, and
  reopen/reopen races.
- View tests for assigned-branch restrictions, field-level errors, expected/actual/variance
  display, cost non-disclosure, pagination, filtering, and internal-report disclaimers.
- Demo-data tests for repeatable open, transaction, movement, and close behavior.
- Independent Claude review of the approved brief, implementation diff, PostgreSQL behavior,
  cash arithmetic, concurrency, idempotency, permissions, and excluded accounting claims.

## Product-owner decision

Approve the recommended Stage 4A scope as written, request changes, or defer it. Approval
authorizes implementation only of this brief and does not authorize Stage 4B expenses or
supplier settlement, Stage 4C stock counts, or Stage 5 profit/margin/growth analytics.
