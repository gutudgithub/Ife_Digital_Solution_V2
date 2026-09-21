# Stage 9A: offline cashier sales continuity

## Status

Recommended for product-owner approval. Not approved and not implemented.

Stage 9A is the first bounded offline slice. It intentionally does **not** make the whole
application offline. It adds installable PWA basics, an offline indicator, a local cashier
sale-draft queue, and a controlled online synchronization workflow that creates ordinary
server sale drafts only after the current server-side rules accept them.

No offline record has ledger effect. Inventory, cash, receipts, performance reports, and
public verification change only after the server accepts the synchronized draft and the
existing online posting flow runs.

## Why this is the next safe slice

The roadmap's Stage 9 goal is offline PWA and synchronization. The current architecture is a
server-rendered Django monolith with strict posting services, PostgreSQL locking,
idempotency keys, branch cash-session rules, moving-average inventory, and tenant-scoped
authorization. A full offline operational system would have to reproduce those rules in the
browser and later reconcile conflicts, which would be unsafe as one stage.

The highest-value offline need for a pilot shop is narrower: a cashier can continue writing
sale drafts during a short connection outage, then synchronize them when the app is online.
Managers and owners can review server-accepted drafts and post them through the ordinary
online sale workflow. This preserves the existing cash and inventory controls.

## In scope

Stage 9A includes:

- PWA manifest and install metadata for the authenticated operations app;
- a service worker that caches only the app shell, safe static assets, login-independent
  offline page, and a small set of authenticated shell pages after they are visited;
- an explicit online/offline status banner visible to authenticated users;
- a cashier-facing offline sale-draft form for the assigned branch;
- local browser storage for pending offline sale drafts;
- client-generated offline draft IDs and idempotency keys;
- a sync screen listing pending, synced, failed, and discarded local drafts;
- an authenticated sync endpoint that validates each submitted draft against current server
  state and creates an ordinary `Sale` in draft status;
- conflict and validation feedback for stale catalog prices, inactive variants, insufficient
  branch visibility, missing assigned branch, changed permissions, duplicate idempotency
  keys, and malformed local data;
- a clear provisional print/share view for local drafts that says it is not an internal
  receipt, not posted, and not proof of payment;
- purge/discard controls for local drafts after sync or explicit operator action;
- tests covering offline storage behavior, synchronization, duplicate replay, tenant
  isolation, conflict handling, permissions, and accessibility basics.

## Explicit exclusions

Stage 9A does **not** include:

- offline posting;
- offline inventory movement;
- offline cash-session movement;
- offline official or internal receipt creation;
- offline purchase, receiving, supplier return, expense, settlement, stock count, attendance,
  document capture, public profile, performance, or administration workflows;
- background sync that runs without the operator reviewing results;
- multi-device merge resolution;
- conflict auto-resolution that changes quantities, prices, branches, payment methods, or
  product variants;
- local storage of cost, assigned inventory value, supplier data, document bytes, staff
  email, Telebirr secrets, private report output, or public-profile moderation evidence;
- customer identity, loyalty, promotions, consent, or ordering;
- payment-provider verification;
- official tax or electronic invoicing;
- native mobile apps.

## Decisions

### Decision 1: offline work creates local drafts only

An offline cashier entry is a local draft held in the browser. It is not a `Sale`, not a
posted sale, not an internal receipt, not a cash movement, and not inventory evidence.

Only successful online synchronization creates a normal server `Sale` in draft status. The
existing online `post_sale` flow remains the only way to create payment evidence, receipt
identity, inventory movements, and cash-session movements.

Rationale: this preserves all server-side stock, cash, receipt, idempotency, and locking
rules.

### Decision 2: synchronize to draft, not posted sale

Synchronization validates current catalog and permission state and creates a server draft.
Posting remains a separate online cashier action. The cashier or manager must still open the
draft, confirm payment method/reference, and post it while online.

If an owner later wants one-click sync-and-post, that must be a separate stage because it
requires explicit decisions about stale stock, stale prices, closed cash sessions, and
receipt numbering after outages.

### Decision 3: one assigned branch per offline cashier

The offline sale form is enabled only when the active cashier has exactly one assigned
branch. Owners and managers may use it only after selecting a specific branch before going
offline.

The local draft stores:

- business ID;
- branch ID;
- active membership role at draft time;
- local draft ID;
- idempotency key;
- local creation time;
- selected variant IDs and display snapshots;
- entered quantities;
- chosen payment method and manually entered Telebirr reference, if any.

The server revalidates all identifiers on sync. Local branch and business IDs are not
trusted for authorization.

### Decision 4: local catalog snapshot is intentionally narrow

The browser may cache only the data needed to render a sale-entry form:

- active product name;
- active variant display label;
- variant public selling price;
- stock unit;
- variant ID;
- last snapshot time.

It must not cache:

- cost;
- inventory balance;
- supplier;
- branch internal codes beyond the authorized selected branch;
- performance output;
- private document metadata or source bytes;
- staff/customer personal data.

Rationale: the cashier can write a draft without exposing manager-only cost or inventory
evidence in local browser storage.

### Decision 5: stock is checked only on online posting

Offline drafting does not reserve stock and does not prove stock availability. Sync may
create a server draft even if stock later becomes insufficient, because ordinary draft
creation has no ledger effect. The existing online posting service remains responsible for
rejecting insufficient stock.

The UI must warn that an offline draft may fail to post later if another sale, return,
stock count, adjustment, or receiving event changes stock before posting.

### Decision 6: catalog price changes are visible conflicts

The offline draft stores the price snapshot seen by the cashier. On sync, if any current
variant selling price differs from the offline snapshot, the server may still create a draft
using current catalog prices, but the sync result must flag the price change before posting.

The cashier or manager must review the server draft before posting. The offline snapshot is
retained as audit context on the sync result, not as an override to current catalog pricing.

### Decision 7: provisional print output is not a receipt

A local draft may be shown or printed only as a provisional sale note. It must say:

> Provisional offline sale note. Not posted. Not an internal receipt. Not payment-provider
> verification. Confirm in Ife after synchronization.

It must not show a receipt number, public verification QR code, or language that suggests
official invoice, tax, payment settlement, or inventory effect.

### Decision 8: local data expires quickly

Pending and failed local drafts remain in the browser for at most seven days unless the
operator discards them earlier. Synced local copies should be removed immediately after the
operator acknowledges the sync result.

If the browser is shared, a signed-out user must not be able to open local draft contents
through app screens. The implementation should clear sensitive local UI state on sign-out
and avoid storing more than the narrow fields in Decision 4.

### Decision 9: idempotency prevents duplicate sync

Every offline draft receives a generated idempotency key. Replaying the same draft to the
sync endpoint must return the same created server draft rather than creating another one.

Reusing one offline key for different local content in the same business must be rejected
with a friendly conflict message.

### Decision 10: conflict handling is explicit

The sync result for each draft is one of:

- `synced`: a server draft was created or replayed;
- `needs_review`: a server draft was created but price or catalog snapshots changed;
- `rejected`: the server refused to create a draft because authorization, tenant, branch,
  product, quantity, payment, or idempotency validation failed;
- `discarded`: the local draft was removed by the operator before sync.

Rejected drafts remain local until the operator edits or discards them. The system must not
silently change quantities, variants, payment method, branch, or business to make them pass.

## Data model implications

The implementation should add a small server-side sync evidence model, for example
`offline.OfflineSaleSync` or a sales-local equivalent, with direct business scope:

- `business`;
- `branch`;
- `actor`;
- `local_draft_id`;
- `idempotency_key`;
- `status`;
- `created_sale` nullable link to `Sale`;
- `offline_created_at`;
- `synced_at`;
- sanitized offline snapshot JSON;
- validation/conflict messages.

Constraints should enforce business-scoped idempotency and prevent one local key from being
reused for different content. The model must not store cost, stock balance, document bytes,
customer identity, browser fingerprint, IP address, or user-agent.

The browser-side queue should live in IndexedDB rather than cookies or server sessions.
Local storage must be versioned so incompatible future schemas can fail closed and ask the
operator to sync or discard old drafts.

## Service design

Synchronization must go through a server service boundary rather than bulk view logic. The
service should:

1. load the actor's active business and membership;
2. reject inactive or unauthorized actors;
3. validate branch scope against the server membership;
4. validate each variant belongs to the same active business and is active;
5. validate quantities using the same decimal boundaries as `SaleLineForm`;
6. validate payment method/reference shape but not provider settlement;
7. obtain or create the sync idempotency record;
8. create an ordinary sale draft using the existing `save_sale_draft` service;
9. record whether current price snapshots differ from offline snapshots;
10. return per-draft results without exposing manager-only cost or inventory evidence.

No sync path may call `post_sale`.

## UI and operator workflow

### Before an outage

1. Operator signs in while online.
2. Operator visits the Sales area.
3. The browser caches the app shell and the authorized sale-entry catalog snapshot.
4. The app shows the last successful snapshot time and offline-readiness state.

### During an outage

1. Operator sees an offline banner.
2. Operator opens the offline sales screen.
3. Operator enters product variants, quantities, and payment method/reference.
4. App saves the local draft with a visible pending status.
5. Operator may print or show a provisional note with the required disclaimer.

### After reconnection

1. Operator opens the sync screen.
2. Operator reviews pending drafts.
3. Operator starts sync manually.
4. App shows each draft as synced, needs review, rejected, or still pending.
5. Operator opens synced server drafts and posts them online through the normal flow.

## Security and privacy

- Service-worker scope must be limited to the authenticated application and must not cache
  private document downloads, performance CSVs, receipt verification pages, public profile
  moderation evidence, or responses marked no-store.
- CSRF protection remains required for sync endpoints.
- Sync endpoints must require authentication and active membership on every request.
- Server-side authorization must ignore business, branch, and role claims submitted from
  the browser except as values to validate against current membership.
- Local data must be minimized and shown only inside authenticated app screens.
- The implementation must not store IP address, user-agent, browser fingerprint, customer
  identity, or public visitor identifiers for offline sync.
- Operators should receive a clear warning that shared devices should not be used for
  offline drafts unless the OS account and browser profile are controlled by the business.

## Accessibility and localization

- Online/offline status must not rely on color alone.
- Sync results must be available as text and announced politely to assistive technology.
- Provisional-note disclaimers and conflict messages must be translation-ready.
- Native-speaker review remains required before accepting Amharic and Afaan Oromoo text.

## Acceptance tests

Automated coverage should include:

1. the PWA manifest and service-worker endpoints are available only with safe methods where
   appropriate and do not cache no-store private downloads;
2. offline draft queue accepts valid variants and quantities in browser tests or focused
   JavaScript tests;
3. local queue rejects negative and zero sale quantities before sync;
4. sync creates a normal server draft with no inventory movement, cash movement, payment
   receipt, or performance effect;
5. replaying the same idempotency key returns the same server draft;
6. reusing the same key for different content is rejected;
7. cashier assigned to another branch cannot sync a draft into an unauthorized branch;
8. inactive membership cannot sync;
9. inactive product or variant is rejected;
10. price change creates a `needs_review` result and the server draft uses current price;
11. insufficient stock does not block draft sync but still blocks online posting through
    the existing sale-posting tests;
12. malformed local JSON cannot crash the endpoint or leak tracebacks;
13. tenant isolation rejects cross-business variant IDs;
14. sign-out or explicit discard clears local visible draft state;
15. service worker does not cache document downloads, performance CSVs, or private no-store
    responses;
16. provisional print output contains the required non-receipt disclaimer.

Full verification should include existing SQLite and PostgreSQL suites plus a focused
browser/PWA test for offline queue and reconnection behavior.

## Rollout gates

Before pilot use:

- HTTPS must be configured; PWA and service workers are not a substitute for deployment
  security.
- Operators must be trained that offline drafts are not posted sales or receipts.
- The business must decide whether provisional sale notes are acceptable for its shop
  process during outages.
- Native-language review must cover the offline warnings and provisional-note disclaimer.
- Existing Stage 5 accountant approval, Stage 7 release gates, and Stage 8 private storage,
  scanner, retention, security, rate-limit, and restore gates remain open.

## Owner approval decision

Approve this recommended Stage 9A scope as written, request changes, or defer it.

Approval authorizes implementation of only this offline sales-continuity slice. It does not
authorize full offline posting, offline stock/cash changes, customer functionality, loyalty,
promotions, official tax/e-invoice behavior, payment-provider integration, or native mobile
apps.
