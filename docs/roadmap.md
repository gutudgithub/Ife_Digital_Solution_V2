# Delivery roadmap

Each phase is a reviewed vertical slice with an approved feature brief, migrations, tests,
operational notes, and independent Claude review.

## Stage 1: foundation hardening and basic attendance

- Django and PostgreSQL-compatible platform
- custom users, businesses, branches, roles, and tenant context
- clothing and footwear catalog
- branch-scoped employee check-in and check-out
- manager correction with immutable before-and-after history
- dashboard, administration, CI, Docker, and documentation

## Complete approved stages

| Stage | Deliverable | Exit evidence |
| --- | --- | --- |
| 2 | Suppliers, purchasing, inventory, and purchase returns | Stock and returns derive correctly from posted movements |
| 3 | Sales, payments, returns, and internal receipts | Atomic and idempotent sale lifecycle passes |
| 4 | Expenses, cash sessions, stock/cash reconciliation | A branch can close a complete business day |
| 5 | Costing and performance intelligence | Accountant-approved formulas and reconciled reports |
| 6 | Customers, loyalty, promotions, and consent | Points ledger and privacy flows pass |
| 7 | Public business profile, QR storefront, and verification | Public controls pass without private-data leakage |
| 8 | Document capture and human-confirmed digitization | Source-to-posted-record traceability passes |
| 9 | Offline PWA and synchronization | Interruption, retry, conflict, and duplicate tests pass |
| 10 | Multi-branch transfers and consolidated control | Branch isolation and transfer lifecycle pass |
| 11 | SaaS plans, onboarding, support, and administration | Subscription lifecycle and safe suspension pass |
| 12 | Localization, accessibility, security, recovery, and pilot | Real users complete normal operations safely |
| 13 | Controlled production launch | Legal, privacy, operations, and acceptance gates pass |

Stage 2 is split into independently reviewable vertical slices so that the inventory ledger
is proven before purchase returns and broader controls are layered onto it.

- Stage 2A is independently approved: suppliers, purchases, partial receiving, opening
  balances, adjustments, moving-average balances, and immutable movement history.
- Stage 2B is independently approved: receipt-linked purchase returns, full reversals,
  supplier activity, purchase-cost history, and filtered movement history.
- Stage 3A is independently approved: fully paid single-tender cash or manually referenced
  Telebirr sales, inventory posting, and internal receipts.
- Stage 3B is independently approved: posted-sale reversals, customer returns, refund
  evidence, return reversals, and exchanges represented as return plus a new sale.

Stage 4 is split into independently reviewable controls:

- Stage 4A is independently approved: shared branch cash sessions, immutable physical-cash
  movements, drawer counting, variance, and controlled reopening.
- Stage 4B is independently approved: paid operating expenses,
  purchase-linked supplier payments, and supplier-return credit/refund evidence.
- Stage 4C has a recommended brief awaiting product-owner approval: complete branch stock
  counts, an inventory-posting freeze, explicitly named quantity variance, count-start
  moving-average valuation, and exact-cost adjustment reversal.

Customer ordering, delivery, professional services, AI, payroll, and statutory employment
administration remain excluded.

Official tax or electronic invoicing has a separate legal and regulatory gate and is not a
routine roadmap continuation.
