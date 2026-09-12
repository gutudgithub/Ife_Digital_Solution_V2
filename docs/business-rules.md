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
2. Implemented movement types include opening balance, purchase receipt, purchase return,
   purchase-return reversal, sale, and authorized positive/negative adjustment. Transfers
   remain a later slice.
3. Negative stock is disallowed by default.
4. Adjustments require a reason, actor, timestamp, branch, and audit event.
5. Replayed requests must not create duplicate movements.
6. A purchase return references the exact posted receipt line and cannot exceed either its
   unreturned received quantity or current branch stock.
7. Supplier reference amounts retain receipt cost while inventory reductions use the current
   branch/variant moving-average cost assigned at return posting.
8. Outbound value is subtracted from stored value and clamped at zero; it is never recomputed
   as remaining quantity multiplied by a rounded average.
9. Posted returns are corrected only by a full linked reversal. The reversal restores
   quantity using the original return movement's assigned inventory cost.
10. Supplier reference totals and inventory-value reductions are operational disclosures,
    not profit, loss, payable, receivable, refund, or purchase-price variance.

## Purchasing

1. Draft purchases and purchase returns have no inventory effect.
2. Approved purchases receive stock only through immutable goods receipts.
3. Received progress and returned progress are derived from immutable receipt and return
   lines rather than editable projection fields.
4. Inactive historical suppliers and variants remain usable for a receipt-linked return when
   tenant, branch, source receipt, and current-stock validations still pass.
5. Supplier payments, refunds, credits, payables, tax, and statutory accounting are not
   represented by the current purchasing records.

## Sales and payments

1. Draft sales do not affect stock, payment evidence, or receipts.
2. A draft copies the current catalog selling price; posting rejects a stale price rather
   than silently repricing it.
3. Posting atomically records immutable line cost evidence, outbound stock movements, one
   exact full cash or manually referenced Telebirr payment, and one internal receipt.
4. Telebirr references are normalized for business-scoped uniqueness but are not provider
   verification or settlement confirmation.
5. Posted sales, lines, payments, and receipts cannot be edited through normal flows.
6. Posted-sale corrections use authorized return, refund, and reversal events referencing
   exact original sale lines; original posted evidence remains immutable.
7. Payment status and sale status are separate.
8. An internal receipt must not be represented as an official tax invoice.

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
