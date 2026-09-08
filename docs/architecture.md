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
- `catalog`: category, product style, and sellable size/color variant.

Future modules should follow ledger boundaries rather than generic CRUD groupings:
inventory, sales, payments, cash, reconciliation, audit, and reporting.

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

| Capability | Owner | Manager | Cashier |
| --- | --- | --- | --- |
| View dashboard and products | Yes | Yes | Yes |
| Create catalog products | Yes | Yes | No |
| Platform administration | Staff permission only | Staff permission only | Staff permission only |

Later slices must define explicit capabilities for posting, voiding, returns, refunds,
adjustments, cash close, employee administration, and report access.

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

## Localization

Source strings use Django translation facilities. English is the development source
language; Amharic and Afaan Oromoo are configured targets. Translation catalogs and
terminology require native-speaker and product-owner review before release.
