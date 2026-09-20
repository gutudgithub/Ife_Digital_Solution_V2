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
11. Starting a complete branch stock count freezes every inventory-posting path until the
    count is approved or cancelled.
12. The snapshot includes every active variant and every inactive variant with nonzero branch
    stock. Every line requires an explicit physical quantity, including zero.
13. Stock employees count blind; only owners and managers see system quantity, average cost,
    inventory value, variance, and calculated value adjustment during review.
14. Stock-count quantity variance is approved physical quantity minus system snapshot.
    Quantity summaries are grouped by stock unit and never add unlike units.
15. Stock-count inventory-value adjustment is quantity variance multiplied by the immutable
    assigned adjustment cost. It is operational evidence, not automatically shrinkage, gain,
    loss, income, expense, profit, tax, or accounting adjustment.
16. Positive variance with a zero snapshot cost requires exceptional unit-cost evidence.
    Nonzero snapshot cost cannot be replaced through the count form.
17. Approved counts are immutable. Corrections use one full exact-cost reversal followed by a
    new complete count when needed.

## Purchasing

1. Draft purchases and purchase returns have no inventory effect.
2. Approved purchases receive stock only through immutable goods receipts.
3. Received progress and returned progress are derived from immutable receipt and return
   lines rather than editable projection fields.
4. Inactive historical suppliers and variants remain usable for a receipt-linked return when
   tenant, branch, source receipt, and current-stock validations still pass.
5. Supplier payments reference exactly one approved or received purchase and cannot exceed
   its derived operational reference balance.
6. Supplier-return credits and refunds reference exactly one posted, unreversed return and
   cannot exceed its supplier-reference amount.
7. These settlement records are operational evidence, not certified payables, supplier
   statements, tax records, or statutory accounting.

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

1. A branch has at most one open session and one session for each Addis Ababa business date.
2. Opening float is physical drawer evidence, not revenue or accounting.
3. Cash sales and refunds require an open session; Telebirr does not affect drawer cash.
4. Expected cash is calculated from immutable opening, sale, refund, added, and removed
   movements.
5. Manual cash added or removed requires an owner or manager and a physical-movement reason.
6. A refund or removal cannot make expected cash negative.
7. Physical cash count is entered separately.
8. Variance is stored explicitly rather than hidden by editing transactions.
9. A non-zero variance requires an explanation.
10. Only an owner or manager may reopen the latest branch session, through an immutable
    event.
11. A return reversal never asserts automatic physical cash recovery.
12. The first real branch session requires an owner/manager physical drawer count and an
    immutable opening-basis note; no historical transaction receives a synthetic session.

## Operating expenses and supplier settlement

1. Draft expenses have no reporting or cash effect.
2. Only fully paid cash or manually referenced Telebirr expenses may be posted.
3. Expense categories are business-scoped; deactivation preserves historical evidence.
4. Posted expenses, supplier payments, and supplier-return settlements are immutable and
   corrected only by full linked reversals.
5. Cash expenses and supplier payments reduce expected cash; recovered supplier cash refunds
   increase it.
6. Cash reversals compensate in the exact original session and cannot move to a later drawer.
7. Supplier-return reference amount, inventory-value reduction, and supplier-accepted
   settlement are retained separately and are not automatically profit, loss, income,
   expense, payable, receivable, or purchase-price variance.
8. A purchase with active supplier payments cannot be cancelled, and a purchase return with
   active supplier settlement cannot be reversed; reverse the linked evidence first.

## Performance intelligence

1. Only active owner and manager memberships may view performance, assigned costs, results,
   graphs, print output, or CSV exports.
2. Reports are scoped to the server-selected business and authorized active branches; a
   submitted cross-business branch is invalid.
3. Report ranges are inclusive and limited to 366 days. Daily, Monday-based weekly, and
   calendar-month buckets are supported.
4. Sales use sale date; returns use return date; return and expense reversals use their Addis
   Ababa local posting date. Corrections do not rewrite earlier periods.
5. Net sales equal posted gross sales minus returns plus return reversals.
6. Net assigned inventory cost uses immutable sale-line assigned cost and exact return and
   return-reversal evidence, never current catalog cost or current moving average.
7. Gross operating result equals net sales minus net assigned inventory cost. Operational
   net result subtracts posted operating expenses net of their reversals.
8. Margin is unavailable when net sales are zero or negative. Growth is unavailable when the
   previous comparable bucket is zero with a nonzero current value or is negative.
9. Cash variance, supplier-reference differences, manual inventory adjustments, stock-count
   adjustments, stock-count reversals, opening stock, drawer additions/removals, supplier
   payments, receipts, and purchase returns are operational controls, not result inputs.
10. Quantities are grouped by stock unit. Unlike units are never added together.
11. “Operational net result” is not statutory net profit, an income statement, tax evidence,
    or certified accounting output.

## Time, currency, and audit

1. The pilot currency is ETB.
2. Monetary values use decimal arithmetic.
3. Timestamps are stored in UTC and displayed in `Africa/Addis_Ababa`.
4. Material actions record actor, business, branch, timestamp, action, target, and relevant
   before/after or event data.
