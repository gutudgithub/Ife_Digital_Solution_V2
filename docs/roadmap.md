# Delivery roadmap

Each phase is a reviewed vertical slice with an approved feature brief, migrations, tests,
operational notes, and independent Claude review.

## Phase 1: foundation

- Django and PostgreSQL-compatible platform
- custom users, businesses, branches, roles, and tenant context
- clothing and footwear catalog
- dashboard, administration, CI, Docker, and documentation

## Phase 2: inventory

- inventory movement ledger
- opening balances and authorized adjustments
- stock-on-hand projection
- negative-stock prevention
- idempotency and concurrency tests

## Phase 3: sales and payments

- draft and atomic posted sales
- product and price snapshots
- payment methods and allocations
- internal transaction receipt
- void, return, reversal, and refund foundations

## Phase 4: cash and reconciliation

- cash-session open and close
- expected cash, physical count, variance, and explanation
- stock counts and discrepancy workflow
- manager approval and audit trails

## Phase 5: reporting and pilot readiness

- daily sales, stock, cash, exception, and employee-activity reports
- exports with tenant and permission controls
- Amharic and Afaan Oromoo terminology review
- backup restoration, monitoring, support, privacy, and security evidence
- controlled clothing and footwear pilot

## Expansion gates

Pilot evidence should determine whether to add public catalog and ordering, customer
accounts, payments, multi-branch operations, delivery, services, referrals, subscriptions,
offline synchronization, and AI.

Official tax or electronic invoicing has a separate legal and regulatory gate and is not a
routine roadmap continuation.
