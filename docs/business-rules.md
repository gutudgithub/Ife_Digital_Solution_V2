# Business rules

## Identity and access

1. A user may belong to more than one business.
2. A user can access a business only through an active membership in an active business.
3. The server selects and validates the active business.
4. Owners and managers may manage the catalog; cashiers currently have read-only catalog
   access.
5. Platform staff status is separate from business membership and does not define an
   operational business role.

## Catalog

1. Categories, products, and variants belong to one business.
2. A product category must belong to the product's business.
3. A variant's product must belong to the variant's business.
4. Product names are unique within a business.
5. Variant SKUs are unique within a business and may be reused by another business.
6. A product represents a style; a variant represents a sellable combination such as size
   and color.
7. Selling and cost prices cannot be negative.
8. Deactivation preserves history; records referenced by operational history should not be
   deleted.
9. Public visibility is stored but has no public-catalog effect in the pilot.

## Inventory

1. Inventory balance is derived from immutable movement history.
2. Movement types include opening balance, purchase receipt, sale, return, transfer, and
   authorized adjustment.
3. Negative stock is disallowed by default.
4. Adjustments require a reason, actor, timestamp, branch, and audit event.
5. Replayed requests must not create duplicate movements.

## Sales and payments

1. Draft sales do not affect stock, cash, revenue, or receipts.
2. Posting a sale atomically records sale lines, stock movements, payment allocation,
   internal receipt, and audit event.
3. Posted sales cannot be edited through normal flows.
4. Corrections use voids, returns, refunds, or reversals with authorization and references to
   the original event.
5. Payment status and sale status are separate.
6. An internal receipt must not be represented as an official tax invoice.

## Cash close

1. Expected cash is calculated from posted movements.
2. Physical cash count is entered separately.
3. Variance is stored explicitly rather than hidden by editing transactions.
4. A non-zero variance requires an explanation.
5. Reopening or correcting a closed session requires elevated authorization and an audit
   event.

## Time, currency, and audit

1. The pilot currency is ETB.
2. Monetary values use decimal arithmetic.
3. Timestamps are stored in UTC and displayed in `Africa/Addis_Ababa`.
4. Material actions record actor, business, branch, timestamp, action, target, and relevant
   before/after or event data.
