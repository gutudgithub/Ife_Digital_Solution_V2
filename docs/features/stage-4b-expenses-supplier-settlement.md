# Ife Digital Solution — Proposed Stage 4B Feature Brief

## Status

Approved by the product owner on 12 September 2026.

## Name

Operating expenses and purchase-linked supplier settlement evidence.

## Outcome

An owner or manager can record a paid operating expense, record partial or complete payments
against an approved purchase, and record a supplier-accepted credit or recovered refund
against an exact posted purchase return.

Every cash event is linked atomically to the branch's open Stage 4A cash session. Telebirr
events retain manually entered reference evidence but do not affect physical cash. Posted
records are immutable and corrected through explicit full reversals.

The business can see operational purchase-reference balances and unresolved supplier-return
amounts without claiming certified accounts payable, profit, loss, tax, provider settlement,
or statutory accounting.

Stage 4B also closes the first-deployment cash boundary identified during independent Stage
4A review: the first real cash session for a branch must start from an authorized physical
count, not an invented reconstruction of pre-Stage-4A transactions.

Stage 4 remains split into:

- Stage 4A: independently approved cash sessions and physical-cash reconciliation;
- Stage 4B: operating expenses and purchase-linked supplier settlement evidence; and
- Stage 4C: stock count and inventory reconciliation.

## Recommended decisions

### 1. Treat a posted operating expense as a paid operational record

Stage 4B records only expenses paid in full at posting. It does not create unpaid bills,
accruals, reimbursement claims, employee advances, or accounts payable.

An operating expense stores:

- business and branch;
- active expense category;
- generated internal number;
- server-assigned Addis Ababa business date;
- optional payee;
- mandatory description;
- positive ETB amount;
- cash or manually referenced Telebirr payment;
- draft, posted, reversed, or cancelled state;
- creator, posting, cancellation, and reversal evidence; and
- timestamps and caller-supplied idempotency relationships.

A draft is editable and has no cash or reporting effect. Posting freezes its category,
description, amount, and payment evidence. A posted expense can be corrected only by a full
reasoned reversal, followed by a replacement expense when needed.

These records feed future operational reporting as "recorded operating expenses." They are
not tax-deductibility decisions, statutory expense recognition, asset classification,
depreciation, payroll, cost of goods sold, or professional accounting entries.

### 2. Start with configurable categories but no approval engine

Owners and managers may create, rename, activate, and deactivate business-scoped expense
categories. Category names are case-insensitively unique inside one business.

An inactive category remains visible on historical expenses but cannot be selected for a
new draft. Categories do not carry tax codes, ledger accounts, depreciation rules, spending
limits, or automatic accounting meaning.

Configurable approval thresholds, approval chains, budgets, recurring postings, templates,
attachments, OCR, and reimbursement workflows remain separately gated. Stage 4B relies on
owner/manager posting authority rather than pretending to provide a complete approval
engine.

### 3. Record supplier payments only against one approved purchase

Each supplier payment must reference one exact purchase in the active business and original
branch. The purchase must be approved, partially received, or received.

Stage 4B permits multiple partial payments but no consolidated payment across purchases.
Each positive payment stores:

- purchase, supplier, business, and branch;
- server-assigned Addis Ababa business date;
- cash or manually referenced Telebirr method;
- optional supplier receipt/reference note;
- amount, reason, actor, and timestamp; and
- a business-wide caller-supplied idempotency key.

A payment cannot exceed the purchase's current operational reference balance. Draft
purchases, cancelled purchases, cross-supplier allocation, overpayments, advances not tied
to a purchase, foreign currency, cheque, bank transfer, and payment allocation across
multiple purchases remain excluded.

The record proves only that the operator recorded value transferred to the supplier. A
Telebirr reference is manually entered evidence, not provider verification or settlement
confirmation.

### 4. Give the three supplier amounts distinct operational meanings

Stage 4B preserves three different amounts rather than forcing them to agree:

1. **Supplier return reference amount** — quantity returned multiplied by the immutable
   original receipt unit cost. This is the expected commercial reference already preserved
   by Stage 2B.
2. **Inventory-value reduction** — quantity returned multiplied by the moving-average cost
   assigned when the return posted. This is internal inventory valuation evidence.
3. **Supplier-accepted settlement amount** — the amount the operator records as actually
   accepted by the supplier as a credit or recovered as a refund.

The third amount may be less than the supplier return reference amount, but cumulative
settlement against one return cannot exceed that reference amount. The interface discloses
all three separately.

Supplier settlement and purchase-reference comparisons use ETB money precision: sum the
underlying six-decimal reference evidence, then quantize once to `0.01` using `ROUND_HALF_UP`.
The original six-decimal supplier and inventory evidence remains unchanged and visible to
authorized users.

Their differences do not automatically mean profit, loss, income, expense, purchase-price
variance, payable, receivable, tax adjustment, or accounting gain/loss. Stage 4B labels the
unresolved difference only as:

```text
unresolved supplier-return reference
= supplier return reference amount
- active supplier-accepted credit/refund amounts
```

No amount is derived from the inventory-value reduction for supplier settlement.

### 5. Resolve a posted supplier return through credit or refund evidence

A supplier-return settlement entry references one exact posted, unreversed Stage 2B purchase
return. It records one of:

- **credit accepted** — a non-cash reduction of the purchase's operational reference amount;
  or
- **refund recovered** — value returned by the supplier through cash or manually referenced
  Telebirr.

Multiple partial entries are allowed up to the return's supplier reference total. Reversed
purchase returns cannot receive new settlement entries, and a purchase return with active
settlement evidence cannot be reversed until that evidence is reversed.

A credit accepted against the source purchase cannot make the operational purchase balance
negative. A refund recovered cannot exceed the purchase's active supplier payments net of
prior active refunds.

Credit notes are internal evidence only; Stage 4B does not claim they are official supplier
tax documents. Refund references are likewise not provider-confirmed.

### 6. Calculate purchase settlement as an operational reference equation

For one purchase:

```text
gross purchase reference
= immutable approved purchase-line totals

accepted return reductions
= active supplier credits
+ active supplier refunds recovered

net purchase reference
= gross purchase reference
- accepted return reductions

net transferred to supplier
= active supplier payments
- active supplier refunds recovered

remaining operational reference balance
= net purchase reference
- net transferred to supplier
```

Examples:

```text
Purchase reference 1,200
Payment 800
Accepted return credit 400
Remaining operational reference balance = 0
```

```text
Purchase reference 1,200
Payment 1,200
Recovered supplier refund 400
Remaining operational reference balance = 0
```

These calculations are derived from immutable active evidence. They are not certified
accounts payable, ageing, a supplier statement, or a general-ledger balance. A negative
remaining balance is rejected rather than silently reclassified as a supplier receivable.

### 7. Integrate exact physical cash effects with Stage 4A

The Stage 4A cash movement vocabulary expands with source-linked movement types:

- operating expense: negative;
- operating expense reversal: positive;
- supplier payment: negative;
- supplier payment reversal: positive;
- supplier refund recovered: positive; and
- supplier refund reversal: negative.

Posting or reversing one of these records in cash:

1. requires and locks the branch's open cash session;
2. creates the source evidence and one linked cash movement atomically;
3. rejects a negative movement that would make expected cash negative; and
4. preserves source-to-movement uniqueness and idempotent replay.

Telebirr creates no physical-cash movement and does not require an open session.

A reversal is an assertion that the original expense/payment/refund record itself was
entered in error. Its cash effect may be negated only in the exact original cash session,
which must be open or first reopened under Stage 4A. The linked compensating movement is
therefore never shifted into a later drawer or business date.

It is not used to represent a later reimbursement, purchase refund, or unrelated cash
recovery; those require their own source evidence. A cash source beneath a newer branch
session cannot be reversed in Stage 4B because its historical session cannot be reopened.

Stage 4B does not use generic cash-added or cash-removed movements as substitutes for a
known expense, supplier payment, or supplier refund.

### 8. Assign online business dates server-side

For cash, the source record inherits the locked cash session's business date. This preserves
the existing Stage 4A rule that a session opened before midnight may continue and close
after midnight without moving its physical events into another business day.

For Telebirr and non-cash supplier credit, posting uses the current Addis Ababa local date.
Operators never submit or override the posting business date.

This online-only rule prevents a late-entered historical event from silently changing a
different drawer or period. Backdated operational events, historical imports, offline
capture, and period reopening require a separate reviewed design. The immutable posting
timestamp and server-assigned business date remain visible.

### 9. Establish the first deployed opening float through a physical count

The first real cash session ever opened for a branch after Stage 4A deployment has no prior
system session to reconcile against.

For that first branch session:

- only an owner or manager may open it;
- the opening float must equal a direct physical count of all cash placed under control of
  that session;
- a nonblank opening-basis note is mandatory;
- the default label is "Physical drawer count at system adoption";
- actor, timestamp, amount, branch, and note are immutable; and
- pre-Stage-4A sales and refunds remain historical evidence and are not synthesized into the
  session.

The opening float may be zero when the direct physical count is zero. It is not revenue,
profit, an owner contribution, a correction, or proof that older transactions reconcile.

After a branch has one session in system history, the existing Stage 4A role policy applies:
an assigned cashier may open the next current-date session, and an opening note is optional.

Demo/test sessions do not define production opening evidence; deployment procedures must
seed no operational sessions.

### 10. Reverse mistakes; do not edit posted evidence

Posted operating expenses, supplier payments, supplier credits, supplier refunds, payment
references, cash movements, posting keys, and reversals are immutable through normal
application and admin flows.

Only an owner or manager may reverse. Reversal requires:

- the exact posted source;
- a nonblank reason;
- a business-wide caller-supplied idempotency key;
- actor and timestamp; and
- an exact compensating operational and, when applicable, physical-cash effect.

A source can be reversed only once. Reversing a supplier settlement entry restores the
derived operational purchase/return balances. A cash source reversal requires the original
linked session to be open and posts compensation to that session only. Reversing a cash
refund also fails if its negative compensating movement would make expected drawer cash
negative.

Partial reversal is excluded. Correct a partially wrong record by fully reversing it and
posting the right replacement.

### 11. Keep Stage 4B outside statutory accounting and tax

Stage 4B does not add:

- general-ledger accounts, debits, credits, journals, trial balance, income statement, or
  balance sheet;
- certified accounts payable, supplier ageing, supplier statement confirmation, or credit
  limits;
- VAT, withholding, tax deductibility, fiscal-device, official invoice, official credit
  note, or electronic-invoicing treatment;
- bank accounts, bank transfers, cheque workflows, bank deposits, bank statements, or bank
  reconciliation;
- Telebirr APIs, provider verification, webhooks, settlement files, or chargebacks;
- assets, capitalization, depreciation, prepaid expenses, accruals, or amortization;
- payroll, wages, employee reimbursement, advances, or statutory employment records; or
- profit, margin, net profit, taxable income, or growth graphs.

Stage 5 may consume posted expense evidence only after the product owner approves explicit
performance formulas and a qualified accountant reviews their intended meaning.

## Roles and permissions

- Owner:
  - manage expense categories;
  - create, edit, cancel, post, view, and reverse operating expenses;
  - post and reverse purchase-linked supplier payments;
  - post and reverse supplier-return credits/refunds;
  - view all branches' operational settlement reports; and
  - perform the first-system-session opening count.
- Manager:
  - the same Stage 4B operational capabilities as the owner inside the active business.
- Cashier:
  - no expense-category, operating-expense, supplier-payment, supplier-return-settlement, or
    settlement-report access;
  - continues to see source-linked signed amounts in an assigned-branch cash session without
    gaining supplier, purchase-cost, or category details; and
  - cannot perform the first-system-session opening.
- Stock employee:
  - continues to see purchase and return inventory evidence allowed by Stage 2;
  - cannot access expense, supplier-payment, refund, credit, or settlement-balance records.
- Platform staff without an active business membership:
  - no Stage 4B operational access.

Every write performs a fresh authorization check. Submitted business, branch, supplier,
purchase, return, category, cash-session, actor, balance, or source identifiers never grant
authority.

## Proposed data model

### `ExpenseCategory`

- UUID primary key;
- explicit business;
- name;
- active state;
- creation and update timestamps; and
- case-insensitive unique name inside one business.

### `OperatingExpense`

- UUID primary key;
- explicit business and branch;
- generated internal number;
- expense category;
- nullable business date assigned by the posting service;
- optional payee;
- mandatory description;
- positive amount;
- draft, posted, reversed, or cancelled state;
- creator and lifecycle audit fields; and
- creation and update timestamps.

### `OperatingExpensePayment`

- UUID primary key;
- one-to-one posted expense;
- explicit business and branch;
- cash or Telebirr method;
- entered and normalized Telebirr reference when applicable;
- exact payment amount;
- posting key, actor, and timestamp; and
- immutable source for the linked cash movement when method is cash.

### `OperatingExpenseReversal`

- UUID primary key;
- one-to-one posted expense;
- explicit business and branch;
- reason;
- posting key, actor, and timestamp; and
- source for an exact compensating cash movement when applicable.

### `SupplierPayment`

- UUID primary key;
- explicit business, branch, supplier, and purchase;
- business date;
- positive amount;
- cash or Telebirr method;
- optional supplier reference/note;
- entered and normalized Telebirr reference when applicable;
- posting key, actor, and timestamp;
- optional one-to-one reversal relationship; and
- immutable source for the linked cash movement when method is cash.

### `SupplierPaymentReversal`

- UUID primary key;
- one-to-one supplier payment;
- explicit business and branch;
- reason;
- posting key, actor, and timestamp; and
- source for an exact compensating cash movement when applicable.

### `SupplierReturnSettlement`

- UUID primary key;
- explicit business, branch, supplier, purchase, and posted purchase return;
- server-assigned business date;
- credit-accepted or refund-recovered type;
- positive accepted amount;
- no payment method for credit;
- cash or Telebirr method for refund;
- optional supplier document/reference note;
- entered and normalized Telebirr reference for a Telebirr refund;
- posting key, actor, and timestamp;
- optional one-to-one reversal relationship; and
- immutable source for a linked positive cash movement when a refund is recovered in cash.

### `SupplierReturnSettlementReversal`

- UUID primary key;
- one-to-one supplier-return settlement;
- explicit business and branch;
- reason;
- posting key, actor, and timestamp; and
- source for an exact compensating cash movement when applicable.

### `ExpenseSettlementPostingKey`

- UUID primary key;
- explicit business;
- caller-supplied UUID;
- operation type;
- source UUID; and
- creation timestamp.

The key is unique inside one business and cannot be reused across expense posting/reversal,
supplier payment/reversal, or return-settlement/reversal operations.

### Stage 4A extensions

- `CashSession` gains an immutable optional opening-basis note.
- First-session opening validates elevated authority and a mandatory note.
- `CashMovementType` gains source-specific expense and supplier movement/reversal values.
- Existing Stage 4A session, movement, closure, reopening, and posting-key evidence remains
  unchanged and is migrated without synthetic history.

## Derived projections

The source ledgers remain authoritative. Services and queries derive:

- posted operating-expense totals by branch, category, and date range;
- gross purchase reference;
- active supplier payments;
- active supplier credits;
- active supplier refunds recovered;
- net purchase reference;
- net transferred to supplier;
- remaining operational purchase reference balance;
- supplier-return reference amount;
- active accepted settlement amount; and
- unresolved supplier-return reference.

Stage 4B does not add an editable payable balance. If a cached projection is later required
for performance, it must have a ledger-rebuild verification path.

## Service boundaries

All Stage 4B operational writes use atomic services.

### Create or edit an expense draft

1. Resolve active owner/manager membership.
2. Resolve category and branch inside the active business.
3. Validate positive money amount and required description.
4. Create or update only a draft with no cash effect.

### Post an expense

1. Lock the draft expense.
2. Revalidate actor, category, branch, amount, and payment evidence.
3. Claim the business-wide idempotency key.
4. For cash, lock the branch's open session before creating evidence.
5. Assign the locked session date for cash or current local date for Telebirr.
6. Reject insufficient expected drawer cash.
7. Create one immutable payment and linked negative cash movement when applicable.
8. Mark the expense posted atomically.

### Reverse an expense

1. Lock the posted expense and payment.
2. Require owner/manager authority, reason, and unused operation key.
3. For cash, require and lock the original linked cash session in open status.
4. Create one immutable reversal and exact positive compensating movement in that session.
5. Mark the expense reversed atomically.

### Post a supplier payment

1. Lock the source purchase.
2. Revalidate business, branch, supplier, approved state, and positive amount.
3. Lock active supplier settlement evidence needed to derive the remaining reference amount.
4. Reject payment above the remaining operational reference balance.
5. Claim the operation key.
6. For cash, lock the open branch session and reject insufficient expected cash.
7. Assign the locked session date for cash or current local date for Telebirr.
8. Create immutable payment evidence and one linked negative cash movement atomically.

An approved purchase with active supplier-payment evidence cannot be cancelled until that
payment evidence is reversed.

### Post a supplier-return settlement

1. Lock the posted, unreversed purchase return and source purchase.
2. Revalidate business, branch, supplier, type, and amount.
3. Lock active return-settlement and supplier-payment evidence.
4. Reject cumulative settlement above the return reference total.
5. For credit, reject a negative resulting purchase reference balance.
6. For refund, reject recovery above active payments net of prior refunds.
7. Claim the operation key.
8. For a cash refund, lock the open branch session.
9. Assign the locked session date for cash or current local date for Telebirr/credit.
10. Create immutable settlement evidence and one linked positive cash movement atomically.

### Reverse supplier evidence

1. Lock the source payment or return-settlement record and its purchase/return.
2. Require owner/manager authority, reason, and unused operation key.
3. Re-derive purchase and return reference balances without the source.
4. For a cash reversal, require and lock the original linked cash session in open status
   and validate any negative compensation.
5. Create one immutable reversal and linked compensating cash movement atomically.
6. Return the same result on exact idempotent replay.

### Open the first branch session

1. Lock the branch and verify that no cash session exists in its history.
2. Require owner/manager authority.
3. Require the direct physical count and nonblank opening-basis note.
4. Create the existing immutable session and opening-float movement atomically.
5. Never inspect or synthesize old sales/refunds to derive the opening amount.

## Interface

Server-rendered Django pages provide:

- expense-category list/create/edit/activate/deactivate;
- expense list with branch/category/status/date/search filters and 50-row pagination;
- expense draft/create/edit/detail/post/cancel/reverse screens;
- purchase detail settlement summary and payment history;
- purchase-linked supplier-payment form;
- purchase-return detail reference/inventory/accepted/unresolved amounts;
- purchase-return credit/refund form and settlement history;
- source links from cash movements to expenses, supplier payments, and supplier refunds;
- supplier activity summary with operational reference labels;
- print-friendly internal expense and supplier settlement evidence;
- accessible field-level and form-level errors; and
- preserved query parameters across pagination.

Every relevant page states that amounts are internal operational evidence, not official tax
documents, certified payables, provider confirmation, or statutory accounting.

Cashiers viewing a cash session see movement type, signed amount, actor, timestamp, and an
authorized generic source label. They do not receive expense descriptions, payees, supplier
identity, purchase costs, supplier contacts, inventory values, or settlement balances.

## Required tests

Tests must cover:

1. expense-category tenant uniqueness, inactive history, role permissions, and ordinary
   duplicate-name form errors;
2. expense draft no-effect, positive amount, server-assigned date, posting, cancellation,
   exact full reversal, replacement, and immutability;
3. cash expense session requirement, negative expected-cash rejection, linked movement,
   atomic rollback, and cash reversal compensation;
4. Telebirr expense reference requirement, normalization, business-scoped uniqueness,
   concurrency, and no cash movement;
5. purchase-state, business, branch, supplier, positive amount, and server-assigned date;
6. partial and complete supplier payments, remaining-reference calculation, overpayment
   rejection, idempotency, concurrency, rollback, and reversal;
7. supplier credit and refund only against an exact posted unreversed purchase return;
8. separate supplier-reference, inventory-value, accepted-settlement, and unresolved amounts;
9. cumulative return settlement limits and concurrent over-settlement protection;
10. credit behavior, refund behavior, refund-not-above-net-payment validation, and resulting
    purchase reference equations;
11. cash supplier payment/refund movements, Telebirr no-movement behavior, open-session
    requirements, and negative compensation rejection;
12. exact source-to-cash-movement uniqueness for posts and reversals;
13. cross-operation posting-key reuse rejection and exact same-key replay;
14. concurrent source posting, concurrent reversal, movement-versus-close, and
    settlement-versus-settlement behavior on PostgreSQL;
15. immutable posted evidence and admin protections;
16. owner/manager authority, cashier/stock-employee denial, platform-staff denial, and fresh
    authorization;
17. tenant and branch isolation for URLs, forms, source identifiers, and reporting;
18. cashier non-disclosure through cash-session source links;
19. first branch session owner/manager requirement, mandatory opening note, direct-count
    zero support, idempotency, and concurrency;
20. later assigned-cashier session opening and optional-note behavior;
21. no synthetic pre-Stage-4A movement/session creation;
22. filtering, pagination, print output, accessible errors, and translation-ready strings;
23. repeatable demo data using service-layer operations; and
24. full PostgreSQL regression and all required quality checks.

## Acceptance criteria

1. Draft expenses have no cash or reporting effect.
2. Every posted expense has exactly one equal full-payment record.
3. Cash expense/payment/refund evidence and its linked movement post or roll back together.
4. Telebirr evidence never affects physical drawer cash.
5. Negative physical movements cannot make expected cash negative.
6. Posted operational and cash evidence cannot be edited or deleted through normal flows.
7. A source can be reversed only once, with immutable reasoned compensation.
8. Supplier payments cannot exceed the active purchase reference balance.
9. Supplier credits/refunds reference exact posted returns and cannot exceed their supplier
   reference amount.
10. Inventory-value reductions never determine supplier settlement amounts.
11. Purchase and return summaries reproduce exactly from immutable active evidence.
12. Concurrent writes cannot overpay, over-settle, duplicate cash movements, or lose updates.
13. The first branch session is an owner/manager-authorized direct physical count with
    immutable opening-basis evidence.
14. No pre-Stage-4A transaction is assigned synthetic drawer history.
15. Cashiers and stock employees cannot access expense or settlement details.
16. No interface claims certified payable, profit/loss, tax, provider confirmation, or
    accounting meaning.
17. Demo setup is repeatable and production deployment seeds no operational records.
18. Ruff, formatting, mypy, migration checks, Django checks, PostgreSQL tests, pre-commit,
    and `git diff --check` pass.

## Explicit exclusions

- unpaid expense bills, accruals, prepayments, reimbursements, advances, and petty-cash
  custody by employee;
- configurable approval thresholds, multi-step approvals, budgets, recurring posting, and
  recurring templates;
- attachments, image capture, OCR, and document transcription;
- expense allocation across branches, purchases, products, or accounting periods;
- asset purchases, capitalization, depreciation, and amortization;
- consolidated supplier payments, cross-purchase credits, overpayments, supplier advances,
  supplier ageing, and credit limits;
- bank accounts, bank transfer, cheque, foreign currency, statement import, and bank
  reconciliation;
- Telebirr APIs, provider verification, webhooks, settlement files, and chargebacks;
- official tax invoices, credit notes, VAT, withholding, fiscal devices, tax filing, and
  electronic invoicing;
- general ledger, journals, chart of accounts, trial balance, certified accounts payable,
  income statement, and balance sheet;
- profit, margin, net profit, taxable income, growth analytics, and time-series graphs;
- stock counts and inventory reconciliation, which remain Stage 4C;
- credit sales, customer receivables, split tender, deposits, and layaway;
- inter-branch transfers, multiple drawers, shifts, handovers, and safe/vault workflows;
- AI, customer ordering, delivery, professional services, payroll, offline posting, bulk
  import, and public supplier ratings.

## Independent review focus

Claude should review the three-amount supplier distinction, purchase-reference equations,
cash-session integration and lock ordering, reversal semantics, first-deployment opening
evidence, tenant/branch isolation, role boundaries, idempotency, PostgreSQL concurrency,
rollback, immutable evidence, cashier non-disclosure, operational labels, and excluded
accounting/tax/provider claims.

## Product-owner decision requested

Approve, revise, or defer this Stage 4B scope. Approval authorizes only the bounded workflow
above. It does not authorize Stage 4C stock counts, Stage 5 performance analytics, statutory
accounting, tax workflows, banking, payment-provider integration, AI, customer ordering,
delivery, or professional services.
