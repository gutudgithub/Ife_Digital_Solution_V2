# Owner-to-cashier operating guide

## Owner or manager setup

1. Sign in and confirm the active business.
2. Create categories, products, size/color variants, selling prices, stock units, and low-stock
   thresholds.
3. Create suppliers and expense categories.
4. Create users and assign active owner, manager, cashier, or stock-employee memberships and
   an operating branch.
5. Record opening inventory only from verified physical evidence.
6. Before the first real cash session, physically count the drawer and record the opening-basis
   note. Do not use the demo seed with real customer data.

## Normal purchasing and inventory workflow

1. Owner/manager creates and approves a purchase.
2. Owner/manager or stock employee receives only physically received quantities.
3. Review inventory balances and immutable movements.
4. Prepare receipt-linked supplier returns when needed; only owner/manager posts or reverses
   them.
5. Record supplier payments, credits, or refunds separately from inventory value.
6. Use manual stock adjustments only with verified evidence and a clear reason.

## Private document capture and transcription

1. Owner/manager opens **Documents → Capture document**, selects the branch and purchase,
   expense, or opening-stock workflow, then uploads one to five JPEG, PNG, or PDF files.
2. Wait for every file to become clean. A scanner error remains quarantined; retry scanning.
   Never transcribe or download a quarantined source.
3. Enter every field manually from the source. Stage 8 performs no OCR, handwriting
   recognition, AI extraction, supplier creation, category creation, or product creation.
4. Submit the completed transcription for owner confirmation. A manager cannot confirm it.
5. The owner compares every source file with the frozen fields and confirms only when they
   match. Confirmation does not prove authenticity and creates no ledger effect.
6. Purchase confirmation creates a normal purchase draft for separate approval and physical
   receiving. Expense confirmation creates a normal expense draft for separate posting.
   Opening-stock confirmation requires a separate atomic posting action.
7. Do not edit a source-derived draft. Before posting, cancel the target and start a
   replacement transcription. After posting, use the target workflow's normal reversal.
8. Use the document detail page to trace source hashes, revisions, confirmation, target
   draft, and posted evidence. Cashiers and stock employees have no document access.

## Start-of-day cash workflow

1. Owner, manager, or cashier opens the assigned branch cash session.
2. Enter the physically counted opening float.
3. Owner/manager may record physical cash added or removed with a reason.
4. Telebirr activity never changes expected drawer cash.

## Cashier sales workflow

1. Open **Sales** and create a sale for the assigned branch.
2. Confirm variant, quantity, current price, and cash or Telebirr method.
3. For cash, confirm a branch cash session is open.
4. For Telebirr, enter the manually observed reference; the application does not verify the
   provider transfer.
5. Post the sale and give the customer the internal receipt. It is not an official tax invoice.
6. Prepare customer-return drafts against exact sale lines. An owner or manager posts refunds,
   full sale reversals, and return reversals.

## Expenses and supplier settlement

1. Owner/manager creates a draft operating expense in the correct category.
2. Post it only when fully paid by cash or manually referenced Telebirr.
3. Cash expenses require the open branch session and reduce expected cash.
4. Reverse incorrect evidence; never edit a posted record.
5. Treat supplier reference, inventory-value reduction, and accepted settlement as separate
   operational values.

## End-of-day cash close

1. Stop physical cash activity and count the drawer.
2. Compare actual cash with expected cash.
3. Explain every nonzero variance.
4. Close the session. Reopening requires owner/manager authorization and preserves the earlier
   closure.
5. A return reversal does not claim that refunded physical cash was recovered.

## Full stock count

1. Owner/manager starts a complete branch stock count. Inventory posting is frozen.
2. Stock employee counts every line blind and enters explicit zero where nothing is found.
3. Submit the complete count.
4. Owner/manager reviews quantity variance by stock unit and ETB value adjustment separately.
5. Approve only supported differences. Corrections use a full exact-cost reversal and a new
   count; approval history is never edited.

## Performance review

1. Owner/manager opens **Performance**.
2. Select an inclusive date range up to 366 days, authorized branch or all branches, and daily,
   Monday-weekly, or calendar-month buckets.
3. Reconcile summary metrics with the exact time-series table, SKU rows, expense categories,
   and current inventory context.
4. Use graphs for trends and the exact table for accessible source values.
5. Review cash variance, unresolved supplier-reference evidence, manual adjustments, and
   stock-count values only in **Operational controls**; they are not included in result.
6. Download time-series or SKU CSV, or print the summary, only for authorized operational use.
7. Read **Operational net result** as a bounded operational measure, not statutory net profit,
   an income statement, a tax return, or certified accounts.

## Role boundaries

- Owner/manager: operational management, posting/reversal authority, costs, values, performance,
  exports, and reconciliation.
- Cashier: assigned-branch sales, return drafts, cash-session open/close, and personal
  attendance; no assigned cost, margin, result, supplier, or inventory-value access.
- Stock employee: assigned-branch receiving, supplier-return drafts, blind count entry, and
  submission; no costs, values, result, approval, or reversal authority.
- Platform staff status alone grants no tenant operational access.

## Owner public-storefront workflow

1. Open **Public profile** and enter only information approved for anonymous publication.
2. Add opening hours and secure HTTPS contact/social links.
3. Select public products. Enable public prices only where the current catalog selling price
   should be visible.
4. Use **Preview** and inspect the profile, product pages, price behavior, and public-data
   disclaimers.
5. Publish only after the readiness panel is complete. Search indexing remains off unless
   the owner explicitly enables it. Search engines and third-party caches may retain earlier
   copies after unpublishing; removal is not instantaneous and may require a separate request
   to each provider.
6. Download SVG QR material or print the poster; keep the visible URL fallback.
7. Submit specific contact, location, or business-document verification requests when
   needed. Verification is evidence review, not legal or government certification.
8. Use **Unpublish** immediately if public information is wrong or exposure must stop.
9. Review aggregate opens as recorded requests, not unique people or customers.

Managers may edit, preview, select products, control public-price visibility, and download
QR material. They cannot publish, unpublish, enable indexing, or appeal. Cashiers and stock
employees have no public-profile management access.
