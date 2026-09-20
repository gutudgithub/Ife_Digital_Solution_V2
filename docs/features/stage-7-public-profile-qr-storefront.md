# Stage 7 feature brief: public business profile, QR storefront, and verification

## Status

Proposed for product-owner approval. Stage 6 customer, loyalty, promotion, points-ledger,
and consent work is intentionally deferred. No Stage 7 implementation is authorized until
the product owner approves the decisions and boundaries in this brief.

Privacy, security, native-language, and production-deployment review remain release gates
even after implementation and independent code review.

## Objective

Stage 7 gives a business a stable, read-only public identity that can be opened from a
share link or printed QR code without exposing private tenant data.

The complete bounded stage will provide:

- an owner-managed publication workflow for one business-level public profile;
- a permanent non-guessable public URL;
- a public profile with voluntarily published contact and location information;
- selected public products and active variants;
- product-level control over whether selling prices are shown;
- downloadable and printable business and product QR codes;
- minimal public verification of internal receipt existence;
- specific evidence-based verification indicators, not an opaque trust score;
- immediate owner unpublish and platform suspension controls;
- authenticated verification requests, corrections, and appeals;
- privacy-minimized aggregate profile, product, and QR-open counts;
- search-engine and social-preview controls;
- immutable publication and verification transition history; and
- tenant-isolation, privacy-leakage, accessibility, localization, and security tests.

The public experience is browsing and identity only. It has no cart, customer account,
order placement, checkout, delivery estimate, payment flow, loyalty, promotion, public
review, or customer-data collection.

## Architecture findings

The existing modular Django monolith already provides:

- a directly business-scoped `Business` record with an active flag and internal slug;
- owner and manager memberships that can manage the catalog;
- business-scoped products, categories, and variants;
- `Product.public_visibility`, which is stored but intentionally has no current public
  effect;
- active flags on products and variants;
- selling prices on variants;
- immutable internal sale and return receipts;
- server-rendered templates with translation markers;
- UUID primary keys for business records; and
- strict tenant filtering on authenticated management routes.

The current code does not provide:

- a public profile or publication state;
- a stable public identifier separate from internal tenant routing;
- public storefront routes;
- public-price policy;
- QR generation;
- verification evidence and status;
- public view analytics;
- publication or verification event history; or
- public-safe receipt verification tokens.

Stage 7 should therefore use a separate `public_profiles` module and separate public URL
namespace. Existing authenticated `/catalog/` views must remain membership-protected.

## Required product and security decisions

### Decision 1: keep Stage 6 deferred

Recommended:

- Stage 7 stores no customer/member profile;
- no visitor login is required;
- no loyalty identity, points, tier, reward, or promotion is displayed or changed;
- no customer consent record is created;
- no public visitor email, phone, name, address, cookie identifier, IP address, or device
  fingerprint is stored; and
- Stage 6 remains a separate future brief and approval.

Business contact details are supplied voluntarily by the business for public display. They
are not customer records.

### Decision 2: use a stable non-guessable public identity

Recommended:

- each public profile receives a dedicated UUID4 `public_id`;
- public URLs use `public_id`, not the internal business slug or sequential identifiers;
- the public identifier is permanent across business-name and internal-slug changes;
- owners cannot edit or recycle it;
- unpublished, inactive, suspended, and unknown identifiers all return the same generic
  not-found response; and
- the route never accepts a business identifier from a form and then trusts it without
  resolving the eligible public profile server-side.

Example route shape:

```text
/p/<public-profile-uuid>/
/p/<public-profile-uuid>/products/<public-product-uuid>/
```

The UUID is a sharing identifier, not authentication. Every public query must still apply
publication and active-state filters.

### Decision 3: use a text-first profile and defer uploaded media

Recommended initial fields:

- public display name;
- short description rendered as plain text;
- public phone;
- public email;
- public website;
- public physical-address text;
- public map link;
- opening hours;
- supported-language labels; and
- optional public social/contact links from an allowed set.

Uploaded logos, cover images, and product photos are deferred. They require a durable media
store, file-type validation, metadata stripping, image re-encoding, size limits, malware
handling, deletion rules, and deployment configuration that the current application does
not yet have.

The storefront will use the business display name and a generated text mark instead of
pretending that local development media storage is production-safe.

No field accepts raw HTML or Markdown. URLs are validated and rendered with safe schemes;
map pages are linked rather than embedded.

### Decision 4: separate business-level public details from operational branches

Recommended:

- Stage 7 publishes one business-level address, map link, contact set, and opening schedule;
- operational `Branch` names, codes, assignments, and activity are not exposed;
- no stock, sales, cash, attendance, expense, supplier, or performance data is grouped or
  exposed by branch; and
- public multi-location profiles wait for the separately gated multi-branch stage.

### Decision 5: owner controls publication; managers prepare content

Recommended authorization:

- owner: edit profile, publish, unpublish, control indexing, request verification, appeal a
  decision, select public products, and control public-price visibility;
- manager: edit draft profile content, preview it, select public products, control
  public-price visibility, and download QR materials after publication;
- cashier and stock employee: no public-profile management access; and
- platform staff: review verification requests and suspend or reinstate public exposure
  only with dedicated Django permissions, without gaining business operational
  permissions.

Only an active owner membership can create a public commitment by publishing or can submit
an appeal in the business's name. A platform suspension overrides owner publication until
an authorized reviewer reinstates it. `is_staff` alone is not verification or suspension
authority.

### Decision 6: define explicit publication eligibility and lifecycle

Recommended publication states:

```text
draft -> published -> unpublished
```

Platform suspension is a separate override rather than a publication state. This preserves
the owner's last publication choice and prevents reinstatement from silently publishing a
profile the owner had left unpublished.

Rules:

- a profile starts in `draft`;
- preview is authenticated and never publicly routable;
- publication requires an active business, public display name, description, and at least
  one public contact method;
- publication does not require a verification badge;
- publishing, unpublishing, suspending, and reinstating use atomic services and row locks;
- repeated requests are idempotent and create no duplicate transition event;
- owner unpublish takes effect immediately;
- `Business.is_active = False` makes the public route unavailable regardless of profile
  state;
- platform suspension makes the public route unavailable, records a private reason, and
  leaves the owner's publication state unchanged;
- reactivation or reinstatement never silently republishes a profile that the owner
  previously unpublished; and
- public responses do not disclose whether a business is unknown, inactive, unpublished,
  or suspended.

### Decision 7: make existing product visibility enforceable

A product is public only when all of the following are true:

```text
business is active
and public profile is published and not suspended
and product is active
and product.public_visibility is true
and at least one related variant is active
```

Public product behavior:

- owner and manager may change `Product.public_visibility` through authenticated forms;
- categories appear only when they contain an eligible public product;
- inactive products and inactive variants are excluded;
- public pages show product name, plain-text description, category, variant size, color,
  stock unit, and optionally selling price;
- internal SKU, reference cost, assigned cost, moving-average cost, stock balance,
  low-stock threshold, supplier data, sales history, and profitability are never exposed;
- no availability or "in stock" claim is shown because exact inventory visibility and
  reservation semantics are not defined;
- products have dedicated non-guessable public UUIDs so product QR links do not expose an
  internal identifier; and
- removing visibility or deactivating the product makes its public detail URL return the
  same generic not-found response.

### Decision 8: control public prices per product

Recommended:

- each product has `show_public_prices`, defaulting to false;
- when true, active variants show their current selling prices in ETB;
- when false, the page says "Contact the business for the current price";
- no historical, discounted, promotion, cost, margin, or comparison price is shown;
- changing a catalog price changes the public current price because Stage 7 is not a quoted
  price or reservation system; and
- every public page states that browsing does not create an order and that off-platform
  contact or payment is not an Ife transaction.

This preserves the already approved policy that each business chooses public-price
visibility per product without pulling promotions forward from deferred Stage 6.

### Decision 9: generate printable QR codes without storing image files

Recommended:

- add one pinned, established pure-Python QR dependency after checking its release age and
  license;
- generate SVG QR responses on demand;
- provide a print-friendly business poster containing the public display name, direct URL,
  QR code, and browsing-only disclaimer;
- provide optional product-specific SVG QR codes and print views;
- encode only HTTPS public URLs derived from the configured canonical public origin;
- never embed private identifiers, contact details, receipt contents, or signed-in URLs in
  QR payloads;
- retain a visible text URL beside every QR code for accessibility and damaged-code
  recovery; and
- avoid storing generated QR image files because the URL is the authoritative identity.

The canonical public origin must be explicit configuration. Production QR generation is
blocked if that origin is absent or not HTTPS. Local development may use an explicitly
configured localhost origin.

### Decision 10: provide privacy-minimized aggregate analytics

Recommended:

- record daily aggregate profile views, product views, and QR-tagged opens;
- store business, optional public product, local business date, source category, and count;
- do not store IP address, user agent, referrer, cookie ID, session ID, or device
  fingerprint;
- use atomic increments and a unique daily aggregate key;
- expose analytics only to owner and manager;
- label counts as total recorded opens, not unique people, verified customers, or trusted
  marketing attribution;
- make clear that bots, repeated opens, and shared links may inflate counts; and
- retain aggregates for a documented bounded period, recommended 24 months, then support
  pruning through an operations command.

The public page performs no customer profiling and sets no analytics cookie.

### Decision 11: use evidence-based specific verification indicators

Recommended indicators:

- `Contact reviewed`;
- `Location reviewed`; and
- `Business document reviewed`, only when platform policy and qualified privacy/legal
  review allow collecting the necessary evidence.

Rules:

- there is no single "trusted", "safe", or "verified business" score;
- there are no public star ratings or reviews;
- only platform staff can approve, reject, revoke, renew, or expire an indicator;
- a business owner can submit a request and can appeal a rejection, revocation, suspension,
  or stale indicator;
- managers may view status but cannot submit or withdraw an owner-level claim;
- evidence collection occurs out of band in this stage; no identity-document upload is
  added;
- the system records a private evidence reference, reviewer, decision time, optional
  expiry, and decision reason;
- the public page shows only indicator type, review date, and expiry when applicable;
- it never exposes evidence references, documents, staff identity, private notes, or
  rejection reasons;
- changing a public contact or location marks the corresponding active indicator stale and
  removes it publicly until re-reviewed;
- expiry removes an indicator publicly without requiring a scheduled mutation; and
- every transition is immutable and auditable.

Indicators confirm only the named review. They do not certify legality, ownership, product
quality, payment settlement, tax status, or transaction safety.

### Decision 12: provide correction and appeal controls without public-data collection

Recommended:

- authenticated owners can request correction of a verification record and appeal a
  platform decision;
- requests have immutable submission evidence and explicit pending, accepted, and rejected
  outcomes;
- public pages provide a configured platform support link for profile correction or abuse
  reporting;
- Stage 7 does not store anonymous public free text, reporter contact data, or attachments;
  and
- an in-app public report form remains deferred until privacy basis, retention, spam
  control, moderation ownership, and safe rate limiting are approved.

This provides an accountable business appeal path without silently introducing customer
consent and public-submission storage from deferred Stage 6.

### Decision 13: add minimal public internal-receipt verification

Recommended:

- each internal sale and return receipt receives a separate immutable random public token;
- printed receipts may include a QR code targeting a public verification page;
- the page reveals only the public business display name, internal receipt identifier,
  issue date, receipt type, and current record status;
- it does not reveal item lines, amounts, payment method, Telebirr reference, branch,
  operator, customer identity, return reason, or other financial evidence;
- reversal status is derived from authoritative immutable sale/return evidence;
- unknown tokens return the same generic not-found response;
- the page repeats that this verifies only that an internal record exists in Ife;
- it does not claim an official tax invoice, provider verification, settlement,
  authenticity of goods, or legal certification; and
- old receipts receive generated tokens through a normal Django data migration without
  rewriting their transactional evidence.

### Decision 14: default to search-engine privacy

Recommended:

- new profiles default to `noindex`;
- only an owner may explicitly allow indexing;
- canonical and Open Graph metadata contain only already-public profile data;
- indexed profiles appear in a generated sitemap only while active, published,
  unsuspended, and owner-enabled for indexing;
- unpublished and suspended pages are excluded immediately;
- no third-party analytics, map embed, font, script, tracking pixel, or social widget is
  loaded; and
- public contact/social links use safe external-link attributes.

Search-engine removal is not instantaneous because external caches and crawlers are outside
Ife's control. The UI must disclose that before indexing is enabled.

### Decision 15: guarantee immediate privacy at the application boundary

Recommended:

- public HTML responses use `Cache-Control: no-store` initially so owner unpublish and
  platform suspension take effect on the next request;
- QR SVGs may be cached because they contain only a stable public URL, but the target page
  rechecks eligibility every time;
- public views never place business-owned private values in page source, comments,
  metadata, structured data, error messages, or analytics records;
- all public text is escaped by Django templates;
- content security policy and deployment-edge rate limits remain Stage 12 production
  controls, but public endpoints receive focused abuse and response-header tests now; and
- logs must not include contact values, evidence notes, or receipt tokens beyond ordinary
  request-path handling approved by deployment policy.

## Proposed data model

All business-owned records carry a direct `business` foreign key.

### `PublicBusinessProfile`

- `id`: UUID primary key;
- `business`: protected one-to-one relation;
- `public_id`: unique immutable UUID4;
- `display_name`;
- `description`;
- optional phone, email, website, address, map link, and approved social/contact links;
- supported languages;
- `publication_status`: draft, published, or unpublished;
- platform-suspension flag and private suspension audit fields;
- `allow_search_indexing`;
- publication, unpublication, and suspension audit fields;
- created and updated timestamps.

### `PublicOpeningHour`

- business and profile;
- weekday;
- closed flag;
- opening and closing local times;
- one row per weekday;
- no overnight interval in the first version.

### `PublicContactLink`

- business and profile;
- allowlisted link type;
- validated HTTPS destination;
- public label and display order;
- active flag.

### Catalog additions

- `Product.public_id`: unique immutable UUID4;
- existing `Product.public_visibility` becomes enforceable;
- `Product.show_public_prices`: false by default.

### `PublicProfileEvent`

Immutable event for:

- profile content update;
- publication, unpublication, suspension, and reinstatement;
- indexing enable/disable;
- product publication and price-visibility changes;
- verification request and decision transitions; and
- verification invalidation after a verified value changes.

The event stores business, profile, action, actor type, optional membership/user, timestamp,
and a bounded before/after snapshot that excludes secret evidence.

### `PublicVerificationRequest`

- business and profile;
- requested indicator type;
- status;
- requester owner membership;
- request reason;
- optional parent request when the record is an appeal;
- timestamps.

### `PublicVerificationDecision`

Immutable staff decision with:

- business and profile;
- request;
- indicator type;
- decision action;
- private evidence reference and reason;
- public review/expiry dates;
- subject-value fingerprint;
- staff actor;
- timestamp.

The latest valid decision determines whether a public indicator is displayed.

### `PublicStorefrontDailyMetric`

- business and profile;
- optional product;
- local date;
- source: direct, shared, or QR;
- metric: profile view or product view;
- nonnegative count;
- unique constraint across the aggregate dimensions.

### `PublicSaleReceiptIdentity` and `PublicReturnReceiptIdentity`

- direct business scope;
- protected one-to-one relation to the immutable internal receipt;
- immutable unique public verification token; and
- creation timestamp.

New identities are created atomically with new receipts. A repository backfill command
creates identities for existing receipts without modifying transactional receipt rows, and
deployment verification fails while any receipt is missing its public identity.

## Service boundaries

All management writes go through typed service functions:

- update profile draft;
- publish and unpublish;
- suspend and reinstate;
- change indexing preference;
- publish/unpublish a product;
- change product price visibility;
- submit verification request;
- decide, revoke, renew, or mark verification stale;
- submit and decide an appeal; and
- increment aggregate storefront metrics.

Services validate business scope, role, profile state, and transition legality. State
transitions lock the affected profile and related request row. Repeated transition requests
are safe and do not create duplicate events.

Public read services return dedicated presentation objects containing only allowlisted
fields. Templates must not receive full business, product-variant, receipt, inventory, or
verification-evidence model objects when doing so could accidentally expose private fields.

## Management and public interfaces

### Authenticated owner/manager pages

- profile draft/edit;
- publication readiness and preview;
- public product and price controls;
- opening-hours editor;
- share URL, QR download, and print poster;
- aggregate storefront analytics;
- verification request/status;
- owner-only publication, indexing, and appeal actions; and
- immutable event history.

### Platform-staff pages

- pending verification queue;
- evidence-reference and decision form;
- approve, reject, revoke, renew, or expire an indicator;
- suspend and reinstate public exposure; and
- review owner correction/appeal requests.

Platform staff access is limited to this public-governance function. It does not grant
catalog, sales, inventory, cash, expense, employee, or performance access.
Dedicated verification and suspension permissions are required in addition to staff login.

### Anonymous public pages

- business profile and opening hours;
- selected categories and products;
- product detail and active variants;
- optional current selling prices;
- specific active verification indicators;
- business and product QR targets;
- minimal receipt-record verification; and
- browsing-only, off-platform-contact, and verification disclaimers.

## Privacy and private-data denylist

Public responses must never expose:

- internal business or branch identifiers, names, codes, or membership assignments;
- user, employee, cashier, stock-counter, manager, or owner identity;
- supplier identity, purchasing, receiving, or settlement evidence;
- internal SKU unless separately approved later;
- exact stock, low-stock threshold, moving-average cost, reference cost, assigned cost, or
  inventory value;
- sale, return, refund, cash-session, expense, stock-count, or performance details;
- customer or loyalty data;
- Telebirr references or payment evidence;
- verification evidence references, documents, private notes, staff identity, rejection
  reasons, or appeal text;
- receipt lines, totals, payment method, branch, or operator; or
- unpublished profile/contact/catalog fields in HTML, metadata, structured data, error
  text, redirects, or analytics.

## Acceptance criteria

Stage 7 is complete only when:

1. an owner can create, preview, publish, immediately unpublish, and republish an eligible
   profile;
2. a manager can prepare content and product visibility but cannot publish, enable indexing,
   submit an owner appeal, or bypass suspension;
3. cashier, stock employee, inactive membership, unauthenticated user, and unrelated tenant
   cannot access management functions;
4. unknown, inactive, draft, unpublished, suspended, and cross-business public identifiers
   all fail without distinguishing private state;
5. only active public products with active variants appear;
6. price-hidden products never leak variant prices in HTML, metadata, QR output, or
   analytics;
7. cost, stock, supplier, customer, receipt-detail, cash, expense, attendance, and
   performance data never appears publicly;
8. owner unpublish and staff suspension affect the next public request;
9. stable business and product QR codes resolve to the correct eligible public page and
   retain a visible text fallback;
10. QR payloads use the configured canonical origin and fail closed for unsafe production
    configuration;
11. aggregate analytics contain no visitor identifier and are clearly labelled as
    non-unique opens;
12. verification badges are staff-decided, specific, expirable, revocable, and automatically
    hidden when their verified subject changes;
13. owners can submit and track correction/appeal requests without exposing private reasons
    publicly;
14. public receipt verification reveals only the approved allowlist and accurately reflects
    reversal state;
15. indexing is disabled by default and sitemap inclusion follows current public
    eligibility;
16. public and management pages are keyboard operable, semantically structured,
    responsive, print-safe where relevant, and translation-ready;
17. every management query and write is tenant-scoped;
18. every business-owned new record carries direct business scope;
19. transition services are atomic, idempotent, audited, and concurrency-tested; and
20. documentation states the exact public fields, verification limits, retention,
    unpublish behavior, and operational response process.

## Verification plan

### Model and service tests

- field, constraint, direct-business-scope, and immutability tests;
- public/profile/product UUID uniqueness;
- role and transition matrix;
- profile publish-readiness validation;
- publish/unpublish/suspend/reinstate idempotency;
- concurrent publication and suspension;
- product-publication and active-variant rules;
- verification request, approval, rejection, expiry, revocation, renewal, staleness, and
  appeal transitions;
- value-change invalidation;
- analytics aggregate uniqueness and concurrent increment;
- receipt-token uniqueness and reversal-state derivation; and
- no delete or unsafe bulk-write path for immutable events and decisions.

### View and security tests

- anonymous public success paths;
- generic not-found behavior for every ineligible state;
- tenant-isolation tests across all management routes;
- owner/manager/cashier/stock/platform-staff permission matrix;
- output allowlist and private-data denylist assertions;
- no price leak when prices are hidden;
- no inactive product or variant leak;
- no internal SKU or operational branch leak;
- safe URL schemes and escaped public text;
- no visitor cookie and no identifying analytics value;
- cache-control, robots, canonical, Open Graph, and sitemap behavior;
- CSRF and POST-only management transitions;
- unsafe-host and unsafe-canonical-origin failures;
- QR target and SVG response tests;
- public receipt minimal-output tests; and
- accessibility landmarks, labels, focus, direct-link fallback, and print output.

### Required repository checks

- affected SQLite tests;
- affected PostgreSQL tests;
- full SQLite suite;
- full PostgreSQL suite;
- Ruff lint and format checks;
- `mypy apps config`;
- Django system and deployment checks;
- migration drift check;
- translation message extraction check;
- pre-commit;
- diff review for unrelated changes; and
- browser testing of owner publishing, manager limitations, public browsing, QR opening,
  price hiding, unpublish, suspension, verification display, and receipt verification.

## Documentation and operations

Implementation must update:

- product scope;
- architecture;
- business rules;
- security and privacy boundaries;
- operations and incident response;
- demo data and walkthrough;
- owner operating guide;
- environment examples for canonical public origin and support link; and
- the roadmap and this brief's final status.

Operations must include:

- immediate owner unpublish procedure;
- platform emergency suspension procedure;
- verification correction, appeal, expiry, and revocation procedure;
- analytics-retention pruning command;
- public-origin and QR smoke test;
- public-data leakage response;
- search-engine removal limitation; and
- backup/restore coverage for profiles, events, decisions, metrics, and receipt tokens.

## Explicit exclusions

Stage 7 does not include:

- customer/member accounts or identity;
- loyalty, points, tiers, rewards, or promotions;
- visitor consent records or marketing subscriptions;
- cart, order, reservation, checkout, delivery, or payment;
- public product reviews, ratings, comments, questions, or chat;
- public visitor report forms or uploaded evidence;
- product availability, reservation, or exact stock;
- discounts or promotional pricing;
- uploaded logos, cover images, product photos, or documents;
- multi-location public branch pages;
- third-party map, analytics, social, SMS, email-verification, or payment APIs;
- an opaque trust score or legal/business certification;
- official tax/electronic-invoice verification;
- provider settlement verification;
- public financial, employee, supplier, or performance data;
- native translated catalogs before native-speaker review; or
- production launch before privacy, security, HTTPS, monitoring, backup/restore, and
  rollback gates pass.

## Approval requested

The recommended complete scope is:

1. keep Stage 6 deferred and collect no public visitor identity;
2. use permanent UUID-based public profile and product links;
3. deliver a text-first profile without uploaded media;
4. publish business-level contact/location details, not operational branches;
5. let managers prepare content while owners control publication, indexing, and appeals;
6. enforce active/public product and variant rules;
7. show variant prices only when enabled per product;
8. generate on-demand SVG business, product, and receipt QR codes;
9. record only non-identifying aggregate opens;
10. use specific staff-reviewed verification indicators with expiry, revocation, staleness,
    correction, and appeal;
11. provide minimal internal-receipt existence verification;
12. default profiles to `noindex` and use no-store public HTML; and
13. retain all ordering, customer, loyalty, promotion, media-upload, public-review, and
    public-submission features outside Stage 7.

Product-owner approval authorizes implementation of this bounded stage, not public
production launch and not any excluded capability.
