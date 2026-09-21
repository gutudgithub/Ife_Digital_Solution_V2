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
- `inventory`: immutable stock movements, moving-average balances, complete blind branch
  counts, reconciliation approvals, and exact-cost reversals;
- `purchasing`: suppliers, purchases, receipts, and purchase returns;
- `sales`: sales, single full payments, exact-line returns, refund evidence, correction
  reversals, and internal receipts.
- `cash`: branch cash sessions, signed physical-cash movements, closure counts, variance,
  and controlled reopening.
- `expenses`: paid operating expenses, purchase-linked supplier payments, supplier-return
  credits/refunds, and their immutable reversals.
- `performance`: read-only owner/manager reporting built from existing immutable evidence,
  current inventory balances, and server-rendered accessible charts and exports.
- `public_profiles`: owner-managed public profile and product projections, stable public and
  receipt identities, reviewed indicators, SVG QR targets, and anonymous aggregate metrics.
- `documents`: private source custody, malware-scan state, typed human transcription,
  immutable revisions, owner confirmation, and operational provenance.
- `offline`: immutable business-scoped synchronization claims and evidence for local
  cashier sale drafts; it creates ordinary `sales.Sale` drafts but never posts them.

Future modules should follow ledger boundaries rather than generic CRUD groupings.

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
| Start, approve, cancel, or reverse stock counts | Yes | Yes | No | No |
| Enter and submit assigned-branch blind stock counts | Yes | Yes | No | Yes |
| View stock-count quantity/cost comparison evidence | Yes | Yes | No | No |
| Open and close assigned-branch cash sessions | Yes | Yes | Yes | No |
| Post manual cash movements or reopen sessions | Yes | Yes | No | No |
| Manage expenses and supplier settlement | Yes | Yes | No | No |
| View performance, assigned costs, result, and exports | Yes | Yes | No | No |
| Prepare public profile content and select products | Yes | Yes | No | No |
| Publish, unpublish, control indexing, and appeal | Yes | No | No | No |
| Upload, transcribe, and review private documents | Yes | Yes | No | No |
| Confirm document transcription | Yes | No | No | No |
| Prepare and synchronize offline sale drafts | Yes | Yes | Assigned branch | No |
| Platform administration | Staff permission only | Staff permission only | Staff permission only | Staff permission only |

Later slices must define explicit capabilities for employee administration.

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

Stage 4C locks the branch before activating a complete stock-count freeze and capturing
immutable quantity, value, and moving-average cost snapshots. Stock employees enter only
blind physical quantities. Managers review quantity variances grouped by stock unit and a
separate signed ETB inventory-value adjustment. Approval posts nonzero differences at the
assigned snapshot or evidenced exceptional cost; full reversal uses the same exact costs.

Stage 5 creates no reporting ledger, snapshot table, or editable KPI row. Its typed read
service selects tenant- and branch-scoped posted source events, applies Addis Ababa event
dates, and returns one immutable result object reused by the dashboard, CSV exports, and
print view. Historical assigned cost comes only from sale and return evidence. Current
inventory context comes from balances. Operational controls are disclosed separately and do
not enter gross or operational result.

Stage 7 stores public projections separately from the private catalog. Immutable UUID4
sidecars prevent internal product and receipt identifiers from entering public URLs. Public
reads use an explicit allowlist, while publication, verification, suspension, and aggregate
metric writes go through typed atomic services. Anonymous metrics store only business,
profile, optional product, local date, source category, metric type, and count.

Stage 8 stores source bytes only through the private `documents` storage alias. Accepted
files use opaque UUID keys, exact hashes, quarantine-first scan states, and authorized
streaming responses with no-store and nosniff headers. Confirmation atomically freezes the
transcription and creates a normal purchase or expense draft, while opening-stock
confirmation remains separate from atomic posting through the existing inventory service.
Source-derived drafts cannot be edited; correction requires cancellation and a preserved
replacement transcription.

Stage 9A keeps local draft and catalog data in a versioned IndexedDB database partitioned by
business and branch. A service worker scoped to `/offline/` caches only an allowlisted shell
and explicitly authorized offline-sales HTML; catalog JSON remains no-store and is copied
only into the tenant/branch-partitioned local store. Synchronization revalidates current
membership, business, branch, variants, decimal quantities, payment shape, age, and
idempotency before calling the ordinary sale-draft service. It never calls sale posting.

## Localization

Source strings use Django translation facilities. English is the development source
language; Amharic and Afaan Oromoo are configured targets. Translation catalogs and
terminology require native-speaker and product-owner review before release.
