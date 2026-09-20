# Stage 5 feature brief: operational costing and performance intelligence

## Status

Proposed for product-owner and accountant approval. No implementation is authorized until
the formula and naming decisions in this brief are approved.

## Objective

Stage 5 turns the immutable operational evidence from Stages 2 through 4C into reconciled,
owner-facing performance intelligence for one business and its branches.

The complete stage will provide:

- owner/manager-only performance access;
- date-range and branch filters;
- daily, weekly, and monthly time buckets;
- net sales, net assigned inventory cost, gross operating result, operating expenses, and
  operational net result;
- gross-margin and operating-margin percentages;
- period-over-period net-sales growth;
- time-series graphs for the approved measures;
- product/SKU performance with quantities kept in their named stock units;
- expense-category analysis;
- current inventory value and low-stock context;
- separate cash, supplier, manual-adjustment, and stock-count control indicators;
- accessible tables behind every graph;
- CSV export and print-friendly internal reports;
- deterministic reconciliation back to immutable source records; and
- tests for formulas, corrections, date bucketing, tenant/branch isolation, permissions, and
  edge cases.

This stage does not create or modify transaction evidence. It reads posted evidence through
a dedicated reporting layer.

## Architecture findings

The existing modular Django monolith already contains the authoritative inputs:

- posted sales retain sale date, selling amount, and assigned inventory cost by line;
- posted customer returns and full sale reversals retain return date, refund amount,
  restored inventory value, and exact-cost reversal evidence;
- posted operating expenses retain business date, amount, and immutable reversal evidence;
- inventory movements retain signed quantity and six-decimal value deltas;
- current inventory balances retain quantity, moving-average unit cost, and inventory value;
- cash sessions retain immutable movements and closure variance;
- supplier payments and supplier-return settlements retain operational reference evidence;
- approved stock counts retain quantity variance and signed value adjustment;
- every source is business- and branch-scoped; and
- owner/manager permissions are already distinct from cashier and stock-employee access.

The new reporting module should therefore aggregate existing evidence instead of copying it
into a second ledger.

## Required accounting and product decisions

### Decision 1: use event-date reporting

Recommended:

- a sale contributes on `sale_date`;
- a return contributes negatively on `return_date`;
- a return reversal contributes positively on the reversal's local posting date;
- an expense contributes negatively on its `business_date`;
- an expense reversal contributes positively on the reversal's local posting date;
- cash variance contributes on the cash session's `business_date`;
- inventory adjustments contribute on their local posting date; and
- stock-count adjustments contribute on the count session's `business_date`, with an
  exact-cost reversal shown on the reversal's local posting date.

Corrections therefore do not rewrite previously displayed periods. They appear as explicit
compensating events in the period when the correction was posted.

### Decision 2: approved sales formulas

For a selected business, branch set, and date range:

```text
gross sales
  = sum of posted sale totals on their sale dates

sale refunds and reversals
  = posted return totals on return dates
    minus exact return-reversal totals on reversal dates

net sales
  = gross sales - sale refunds and reversals
```

The phrase "sale refunds and reversals" is a signed reporting measure. A posted return adds
to the deduction; reversing that return subtracts from the deduction.

Cancelled drafts and unposted records contribute zero.

### Decision 3: approved assigned-cost formulas

```text
gross assigned inventory cost
  = absolute assigned inventory value of posted sale lines

returned assigned inventory cost
  = inventory value restored by posted returns
    minus exact inventory value removed by return reversals

net assigned inventory cost
  = gross assigned inventory cost - returned assigned inventory cost
```

The report uses the immutable assigned costs captured by sale and return posting. It never
revalues historical sales using a current catalog reference cost or current moving average.

### Decision 4: approved performance formulas

Recommended safe terminology and formulas:

```text
gross operating result
  = net sales - net assigned inventory cost

net operating expenses
  = posted operating expenses - posted expense reversals

operational net result
  = gross operating result - net operating expenses

gross margin percentage
  = gross operating result / net sales × 100

operating margin percentage
  = operational net result / net sales × 100
```

Percentages are shown only when net sales are greater than zero. Otherwise they are shown as
"Not available" rather than infinity or a misleading percentage.

The recommended UI name is **Operational net result**, not **Net profit**, because the
current system excludes payroll, depreciation, financing, taxes, accruals, owner drawings,
and other statutory-accounting classifications. If an accountant explicitly approves the
formula and omissions for the pilot, the visible name may be changed to **Pilot net profit**
with the same disclosed scope.

### Decision 5: approved growth formula

For each daily, weekly, or monthly bucket:

```text
net-sales growth percentage
  = (current bucket net sales - previous comparable bucket net sales)
    / previous comparable bucket net sales × 100
```

Rules:

- when both buckets are zero, growth is `0%`;
- when the previous bucket is zero and the current bucket is nonzero, growth is "Not
  available";
- when the previous bucket is negative, growth is "Not available";
- comparison uses the immediately preceding bucket of the same duration; and
- the first displayed bucket may query one preceding bucket solely to calculate comparison.

Growth is not compounded annual growth and is not a forecast.

### Decision 6: keep operational differences outside performance formulas

The following remain separate controls and are not included automatically in gross or
operational result:

- physical-cash variance;
- supplier reference versus inventory-value difference;
- manual inventory adjustment value;
- stock-count quantity variance;
- stock-count inventory-value adjustment;
- opening stock;
- cash added or removed manually;
- supplier payments and supplier refunds; and
- purchase receipts and purchase returns.

Supplier payments are settlement activity, not a second expense. Purchase receipts and
returns change inventory value; assigned sale cost recognizes inventory value in the
performance formula when goods are sold.

Stage 5 will show these excluded measures in a clearly separated **Operational controls**
section so the owner can investigate them without accidentally adding them to performance.

### Decision 7: preserve unlike quantity units

Product and stock summaries must group quantities by SKU and stock unit. Pieces, pairs,
grams, and unlike units are never added into one total.

Money may be totaled in ETB.

### Decision 8: owner/manager-only access

Only active owner and manager memberships may view or export performance intelligence.

Cashiers and stock employees must receive permission denial and must not see:

- assigned inventory cost;
- gross or operating result;
- margin;
- inventory value;
- product profitability; or
- cross-branch performance.

### Decision 9: report timing and timezone

- Business dates remain the primary dates for transactions that already carry them.
- Posting datetimes used for reversal events are converted to `Africa/Addis_Ababa` before
  deriving a reporting date.
- Weekly buckets start Monday.
- Monthly buckets use calendar months.
- Default range is the current calendar month.
- A single request is limited to 366 displayed days.

### Decision 10: deterministic reporting, no second ledger

The reporting service reads immutable source records and returns typed value objects.

Stage 5 will not add editable KPI rows, mutable report snapshots, or background jobs that
can drift from source evidence. CSV and print output are generated from the same report
object as the HTML screen.

Database indexes may be added for business, branch, status, and reporting-date access, but
source records and posting services remain unchanged.

## Functional scope

### 1. Performance dashboard

Add an owner/manager-only **Performance** navigation item and dashboard with:

- start date;
- end date;
- branch, including an all-branches option;
- bucket size: daily, weekly, or monthly;
- apply and clear actions; and
- validation for date order and maximum range.

KPI cards:

- gross sales;
- sale refunds and reversals;
- net sales;
- net assigned inventory cost;
- gross operating result;
- net operating expenses;
- operational net result;
- gross margin percentage;
- operating margin percentage; and
- net-sales growth versus the immediately preceding comparable range.

Every KPI includes a short formula or source label.

### 2. Time-series graphs

Server-rendered accessible graphs show:

- net sales;
- gross operating result;
- operational net result;
- gross margin percentage;
- operating margin percentage; and
- net-sales growth percentage.

Requirements:

- no new client-side chart dependency;
- scalable SVG or equivalent server-rendered output;
- visible axis labels and ETB/percentage units;
- no color-only distinction;
- a text/table equivalent containing the exact plotted values;
- graceful empty state;
- negative values rendered clearly;
- no invented interpolation between buckets; and
- print output that remains readable without JavaScript.

### 3. Product/SKU performance

For each visible product variant:

- product name;
- SKU, size, color, and stock unit;
- gross quantity sold;
- returned/reversed quantity;
- net quantity sold;
- gross sales;
- return/reversal deduction;
- net sales;
- net assigned inventory cost;
- gross operating result;
- gross margin percentage; and
- link to filtered sale evidence where practical.

Quantities are never totaled across unlike stock units.

The table supports search, ordering, and pagination.

### 4. Expense analysis

For each expense category:

- posted expense amount;
- reversed amount;
- net operating expense;
- percentage of total net operating expenses when the denominator is positive; and
- drill-down to source expense evidence.

### 5. Inventory costing context

Show point-in-time current context, not a historical balance-sheet claim:

- current on-hand quantity by stock unit;
- current inventory value;
- current moving-average unit cost by SKU;
- out-of-stock variant count;
- low-stock variant count; and
- timestamp stating when the screen was generated.

Current inventory value is shown separately from selected-period performance.

### 6. Operational controls

Show separately:

- latest active cash-closure variance by business date;
- unresolved supplier-return reference evidence;
- manual inventory adjustment value by event date;
- approved stock-count adjustment value by count date;
- exact stock-count reversal value by reversal date; and
- source links and explanations.

The section must repeat that these controls are excluded from operational result.

### 7. Export and print

Provide:

- CSV export of time buckets;
- CSV export of SKU performance;
- print-friendly performance summary; and
- formula/version metadata in every export.

Exports must preserve decimal precision, use explicit column names, and respect the same
tenant, branch, date, and role filters as the screen.

## Proposed implementation

Add an `apps.performance` Django application with:

- `forms.py` for validated report filters;
- `services.py` for typed deterministic aggregation;
- `views.py` for HTML, CSV, and print endpoints;
- `urls.py`;
- template tags or presentation helpers only when formatting cannot remain in templates;
- server-rendered templates and accessible charts;
- tests for services, views, permissions, exports, and reconciliation; and
- no writable business models unless query-performance evidence later proves a cache is
  necessary.

Add `can_view_performance` to `BusinessMembership`, restricted to owner and manager.

The services must accept an explicit business and authorized branch selection. They must not
trust raw business or branch identifiers from query parameters.

## Reconciliation invariants

1. Every KPI is derived from business- and branch-scoped source rows.
2. Draft and cancelled records contribute zero.
3. Posted originals remain in their original bucket.
4. Corrections contribute in their correction-date bucket.
5. Summed time buckets equal the selected-range KPI totals.
6. Summed SKU rows equal the selected-range sales and assigned-cost totals.
7. Summed expense-category rows equal selected-range net operating expenses.
8. Return reversal value exactly negates its return's assigned-cost restoration.
9. Decimal operations preserve stored precision and use `ROUND_HALF_UP` only at documented
   display boundaries.
10. No float enters money, quantity, percentage, chart coordinates derived from business
    values, or CSV calculations.
11. All-branch totals include only branches in the active business.
12. Cashier and stock-employee requests disclose no report values.
13. Empty ranges return zeros and "Not available" percentages without server errors.
14. A zero or negative comparison denominator never produces infinity or an exception.
15. HTML, print, and CSV totals are produced from the same service result.

## Required tests

### Formula tests

- sale only;
- partial customer return;
- full sale reversal;
- return followed by exact-cost return reversal;
- mixed cash and Telebirr sales;
- operating expense and expense reversal;
- zero net sales;
- negative net sales caused by later-period returns;
- zero and negative growth denominators;
- six-decimal assigned-cost aggregation;
- `ROUND_HALF_UP` display behavior; and
- no float usage.

### Time tests

- daily, Monday-based weekly, and calendar-month buckets;
- correction on a later date;
- Addis Ababa date at UTC-day boundaries;
- leap day;
- inclusive start/end dates;
- previous comparable period; and
- 366-day range limit.

### Security and scope tests

- owner access;
- manager access;
- cashier denial;
- stock-employee denial;
- inactive membership denial;
- platform-staff denial without tenant membership;
- cross-business branch rejection;
- cross-branch filtering;
- CSV permission parity; and
- no cost/result leakage in shared navigation or errors.

### Reconciliation tests

- KPI totals equal bucket totals;
- KPI totals equal SKU totals where applicable;
- expense totals equal category rows;
- current inventory value equals branch inventory balances;
- latest active closure only for cash variance;
- stock-count approval and reversal shown separately;
- manual adjustments excluded from result; and
- supplier payment/settlement activity excluded from expenses and result.

### Interface tests

- translated labels;
- accessible filter errors;
- graph text alternatives;
- negative-value presentation;
- empty state;
- pagination/filter preservation;
- CSV headers and decimal values;
- print disclaimer; and
- no statutory profit, tax, provider-confirmation, or audited-accounting claim.

## Demo scenario

Extend `seed_demo` idempotently with enough dated evidence to show:

- at least three time buckets;
- sales in cash and Telebirr;
- a later customer return;
- a return reversal;
- operating expenses in more than one category;
- an expense reversal;
- positive, zero, and unavailable growth examples;
- current inventory value;
- one cash variance;
- supplier-reference evidence; and
- approved and reversed stock-count adjustment evidence where safe.

The demo must use posting services and remain disabled outside debug mode.

## Documentation updates

Update:

- README status and routes;
- product scope;
- architecture;
- business rules;
- data model;
- security/privacy;
- operations and staging smoke checks;
- roadmap;
- owner-to-cashier guide;
- Claude review guide; and
- translation-review reminder.

## Explicit exclusions

- statutory or certified net profit;
- income statement, balance sheet, cash-flow statement, or general ledger;
- VAT, withholding, corporate tax, tax filing, or official tax invoice;
- payroll, depreciation, amortization, financing cost, interest, loans, and owner equity;
- accruals, prepayments, unpaid bills, receivables, payables, and inventory write-down policy;
- bank or Telebirr provider reconciliation;
- automatic classification of cash, supplier, manual-stock, or stock-count differences as
  income, expense, gain, loss, theft, or shrinkage;
- forecasts, budgets, targets, recommendations, anomaly detection, or AI;
- customer, loyalty, promotion, consent, or public-storefront features;
- multi-branch transfers;
- external BI integrations;
- editable reports or manual KPI overrides; and
- final native-language acceptance without native-speaker review.

## Acceptance criteria

1. Owner/manager can filter one branch or all active-business branches over a valid date
   range.
2. Cashier, stock employee, inactive member, and unrelated platform staff cannot access any
   performance endpoint or export.
3. Net sales reconcile exactly to posted sale, return, and return-reversal evidence by event
   date.
4. Net assigned inventory cost reconciles exactly to immutable sale/return assigned costs.
5. Gross operating result equals net sales minus net assigned inventory cost.
6. Net operating expenses reconcile to expense and expense-reversal evidence by event date.
7. Operational net result equals gross operating result minus net operating expenses.
8. Margin and growth denominator rules never raise or misstate infinity.
9. Daily, weekly, and monthly buckets sum exactly to selected-range totals.
10. Product rows reconcile to sales KPIs without mixing stock units.
11. Current inventory value reconciles to current inventory balances.
12. Cash, supplier, manual-adjustment, and stock-count indicators remain visibly separate
    and excluded from performance formulas.
13. HTML, CSV, and print output share one deterministic service result.
14. All source queries enforce active-business and authorized-branch scope.
15. Every graph has an exact accessible table alternative.
16. No screen or export claims certified accounting, tax, provider verification, audited
    profit, or unexplained loss.
17. Demo seeding is repeatable.
18. PostgreSQL and SQLite affected and full suites pass.
19. Ruff, formatting, mypy, migration drift, Django checks, deployment checks, pre-commit,
    and `git diff --check` pass.
20. Claude independently reviews the complete package and reports no blocking findings.

## Approval gate

Implementation requires explicit approval of:

1. event-date correction reporting;
2. net-sales formula;
3. assigned-cost formula;
4. the **Operational net result** label or an accountant-approved alternative;
5. gross and operating margin formulas;
6. net-sales growth formula and zero-denominator behavior;
7. exclusion of cash, supplier, manual-adjustment, and stock-count differences;
8. owner/manager-only access;
9. the complete dashboard, graph, product, expense, inventory-context, controls, export, and
   print scope; and
10. all explicit exclusions.

Approval authorizes the complete Stage 5 implementation described here. It does not
authorize statutory accounting, tax, payroll, banking, AI, customer ordering, delivery,
professional services, or any later roadmap stage.
