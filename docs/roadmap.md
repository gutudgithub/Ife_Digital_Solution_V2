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

## Approved roadmap sequence and current status

| Stage | Deliverable | Status and exit evidence |
| --- | --- | --- |
| 2 | Suppliers, purchasing, inventory, and purchase returns | Independently approved; stock and returns derive correctly from posted movements |
| 3 | Sales, payments, returns, and internal receipts | Independently approved; atomic and idempotent sale lifecycle passes |
| 4 | Expenses, cash sessions, stock/cash reconciliation | Independently approved; a branch can close a complete business day |
| 5 | Costing and performance intelligence | Implemented and independently approved; accountant review remains required before pilot decision use |
| 6 | Customers, loyalty, promotions, and consent | Deferred by the product owner; points-ledger and privacy work remains separately gated |
| 7 | Public business profile, QR storefront, and verification | Implemented and independently approved; release gates remain |
| 8 | Document capture and human-confirmed digitization | Product-owner approved and implemented; independent review and production policy gates remain |
| 9 | Offline PWA and synchronization | Planned; Stage 9A proposed as offline cashier sales continuity |
| 10 | Multi-branch transfers and consolidated control | Planned; branch isolation and transfer lifecycle must pass |
| 11 | SaaS plans, onboarding, support, and administration | Planned; subscription lifecycle and safe suspension must pass |
| 12 | Localization, accessibility, security, recovery, and pilot | Planned; real users must complete normal operations safely |
| 13 | Controlled production launch | Planned; legal, privacy, operations, and acceptance gates must be approved |

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
- Stage 4C is independently approved: complete
  branch stock counts, an inventory-posting freeze, explicitly named quantity variance,
  count-start moving-average valuation, and exact-cost adjustment reversal.

Stage 5 is independently approved as one complete owner/manager performance-intelligence
stage: reconciled net sales, assigned cost, gross and operational result, margin, growth,
accessible time-series graphs, SKU and expense analysis, inventory context, separately
disclosed operational controls, CSV, and print output. Accountant review of the formulas,
naming, and exclusions remains outstanding before pilot decision use.

Stage 6 remains on the roadmap but is intentionally deferred. No customer, loyalty,
promotion, points-ledger, or consent functionality is authorized as part of Stage 7.

Stage 7 is an implemented and independently approved public read-only business identity and
catalog-browsing stage. It does not add customer accounts, ordering, checkout, payment,
delivery, loyalty, promotions, public reviews, or public access to operational records.
Privacy, security, native-language, and deployment review remain outstanding.

Stage 8 is implemented as private online source-document capture with signature validation,
quarantine and scanning, structured human transcription, owner confirmation, immutable
revision evidence, and explicit links to ordinary purchase, expense, or opening-stock
workflows. OCR, handwriting recognition, automatic posting, public uploads, and offline
capture remain excluded. Independent Claude review, production private-object storage,
malware-scanner operations, retention/legal approval, security/privacy review, and
native-language review remain release gates.

Stage 9 should be split into independently reviewable offline slices. The recommended first
slice, Stage 9A, is offline cashier sales continuity: installable PWA basics, a narrow local
sale-draft queue, manual synchronization to ordinary server sale drafts, and explicit
conflict results. It does not authorize offline posting, inventory movement, cash-session
movement, receipt creation, document capture, attendance, stock counts, expenses, or public
profile workflows.

Customer ordering, delivery, professional services, AI, payroll, and statutory employment
administration remain excluded.

Official tax or electronic invoicing has a separate legal and regulatory gate and is not a
routine roadmap continuation.
