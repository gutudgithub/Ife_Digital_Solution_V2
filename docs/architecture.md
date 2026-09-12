# Architecture

## Shape

Ife is a modular Django monolith:

- Django templates provide the initial web interface.
- HTMX may be added for focused interaction where it reduces complexity.
- Django REST Framework should be introduced only for a justified external or asynchronous
  interface.
- PostgreSQL is the production database; SQLite is a local-development fallback.
- Gunicorn and WhiteNoise provide the current container runtime and static-file delivery.

The initial modules are:

- `accounts`: custom email-based user identity;
- `businesses`: business, branch, membership, role, and active tenant context;
- `catalog`: category, product style, and sellable size/color variant;
- `inventory`: immutable stock movements and moving-average balances;
- `purchasing`: suppliers, purchases, receipts, and purchase returns;
- `sales`: sales, single full payments, exact-line returns, refund evidence, correction
  reversals, and internal receipts.
- `cash`: branch cash sessions, signed physical-cash movements, closure counts, variance,
  and controlled reopening.
- `expenses`: paid operating expenses, purchase-linked supplier payments, supplier-return
  credits/refunds, and their immutable reversals.

Future modules should follow ledger boundaries rather than generic CRUD groupings:
reconciliation, audit, and reporting.

## Tenant boundary

Every business-owned row includes a direct `business_id`, even where ownership could be
inferred through another relation. This makes authorization filters explicit and enables
business-scoped database constraints such as SKU uniqueness.

`ActiveBusinessMiddleware` resolves only active memberships belonging to the authenticated
user. Views derive business scope from that server-side context, not submitted form data.
Related records validate that their business identifiers match.

This is application-level multitenancy in one database and schema. A future move to
schema-per-tenant or database-per-tenant requires a separate architecture decision.

## Authorization

Current role capabilities:

| Capability | Owner | Manager | Cashier | Stock employee |
| --- | --- | --- | --- | --- |
| View dashboard and products | Yes | Yes | Yes | Yes |
| Manage catalog and suppliers | Yes | Yes | No | No |
| Post assigned-branch sales | Yes | Yes | Yes | No |
| Prepare customer-return drafts | Yes | Yes | Yes | No |
| Post refunds or reverse sale corrections | Yes | Yes | No | No |
| View sale inventory cost/value | Yes | Yes | No | No |
| Receive purchases and prepare supplier returns | Yes | Yes | No | Yes |
| Adjust stock or post/reverse supplier returns | Yes | Yes | No | No |
| Open and close assigned-branch cash sessions | Yes | Yes | Yes | No |
| Post manual cash movements or reopen sessions | Yes | Yes | No | No |
| Manage expenses and supplier settlement | Yes | Yes | No | No |
| Platform administration | Staff permission only | Staff permission only | Staff permission only | Staff permission only |

Later slices must define explicit capabilities for employee administration and report access.

## Financial architecture

Operational documents and ledger events must be distinct:

- a draft document has no financial or stock effect;
- posting validates permissions and invariants inside one database transaction;
- posting creates immutable ledger movements and an audit event;
- a caller-supplied or generated idempotency key prevents duplicate posting;
- correction creates an authorized compensating event rather than editing history.

Money uses fixed-precision decimals. Quantities must use an explicit unit and decimal
precision appropriate to that unit. Concurrency-sensitive stock and cash operations must
lock or atomically update the affected records.

Stage 3A sale posting locks the sale and inventory resources, rejects stale catalog prices,
records one full cash or Telebirr payment, emits one outbound movement per line, and creates
one internal receipt in a single transaction. Stage 3B preserves that evidence and records
returns through exact source-line compensating events, exact refund evidence, and internal
return receipts. Return posting restores stock at the original sale-line assigned cost;
reversing a return removes the same quantity and inventory value at that assigned cost, then
recalculates the remaining moving average. Neither receipt is an official tax invoice or tax
credit note.

Stage 4A cash posting locks the open branch session for physical-cash sales, refunds, manual
drawer movements, closure, and reopening. Expected cash is the exact signed movement sum;
the separately entered physical count and immutable variance snapshot never rewrite source
transactions. Telebirr and historical pre-Stage-4A evidence are not assigned drawer effects.

Stage 4B posts only fully paid expenses and purchase-linked supplier settlement evidence.
Cash methods create source-linked movements in the locked session; Telebirr and supplier
credits do not affect physical cash. Supplier reference, inventory-value reduction, and
accepted settlement remain distinct operational values rather than statutory accounting.
Reversals compensate in the original cash session, which must first be validly reopened.

## Localization

Source strings use Django translation facilities. English is the development source
language; Amharic and Afaan Oromoo are configured targets. Translation catalogs and
terminology require native-speaker and product-owner review before release.
