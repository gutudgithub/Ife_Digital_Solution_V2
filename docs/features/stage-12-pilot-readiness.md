# Stage 12: pilot-ready catalog, payments, localization, accessibility, security, and recovery

## Status

Proposed for product-owner approval. No Stage 12 implementation is authorized until this
brief is approved.

The product owner has chosen to defer Stages 10 and 11 for this round and proceed directly
to one final pre-pilot stage. Stage 12 therefore combines the roadmap's existing
localization, accessibility, security, recovery, and pilot gates with two approved product
directions:

- responsive image-card catalogs for internal and public use; and
- a branch Telebirr merchant QR with manually observed transaction-reference evidence.

This stage prepares one controlled business and branch for supervised pilot use. It does not
authorize unrestricted production launch.

## Why this scope is achievable

The existing application already has:

- tenant- and branch-scoped product and variant records;
- role-specific cost visibility;
- owner-controlled public product publication and price visibility;
- cash and manually referenced Telebirr sale posting;
- public QR generation;
- private upload validation and storage patterns;
- an authenticated PWA shell and offline sale-draft continuity;
- translation-ready Django templates; and
- documented production, backup, restore, security, and pilot gates.

Stage 12 can extend those boundaries without changing the ledgers. Product images remain
catalog presentation data. A merchant QR helps the customer initiate payment but does not
alter the existing rule that the cashier must enter a unique Telebirr reference and that
Ife makes no provider-verification claim.

## In scope

Stage 12 includes:

1. one optional primary image for each product;
2. secure image validation, metadata stripping, resizing, replacement, removal, storage,
   and tenant-scoped delivery;
3. responsive product-card grids for the internal catalog and public storefront;
4. an accessible visual product picker for normal online sale-draft entry, retaining the
   server-rendered form and native controls as the non-JavaScript fallback;
5. strict cost and private-field visibility by role and publication state;
6. one owner-controlled Telebirr merchant QR profile per branch;
7. Telebirr QR display on the authenticated sale-posting workflow and a printable in-store
   payment card;
8. continued manual Telebirr transaction-reference entry and uniqueness enforcement;
9. a safe staff-entry QR that opens the HTTPS login page in a browser without containing
   credentials;
10. explicit language switching and complete translation-catalog generation;
11. native-speaker review and product-owner acceptance of Amharic and Afaan Oromoo;
12. WCAG 2.2 AA-oriented keyboard, focus, contrast, semantics, error, zoom, reflow, and
    screen-reader remediation;
13. mobile and desktop browser acceptance, including real Android and iPhone checks;
14. content-security, session, authentication-abuse, upload, dependency, tenant-isolation,
    and security-header hardening;
15. production readiness checks for HTTPS, hosts, cookies, CSP, rate limiting, object
    storage, malware scanning, retention approval, backups, and monitoring;
16. documented backup and isolated restore evidence for PostgreSQL and stored objects;
17. pilot training, acceptance scenarios, support, incident, rollback, and go/no-go
    evidence; and
18. independent Claude review of the approved brief, implementation, tests, and pilot
    evidence.

## Explicit exclusions

Stage 12 does **not** include:

- Telebirr API calls, webhooks, provider-status verification, settlement files, chargebacks,
  automatic reconciliation, dynamic amount QR generation, or provider-led refunds;
- public ordering, cart, checkout, delivery, customer accounts, loyalty, promotions, or
  consent profiles;
- multi-branch stock transfers or consolidated transfer control from Stage 10;
- subscription plans, billing, self-service onboarding, or support administration from
  Stage 11;
- native Android or iOS applications;
- offline posting, offline payment verification, or offline storage of Telebirr QR images;
- multiple product galleries, video, animated images, owner-authored HTML, or remote image
  URLs;
- variant-specific images in the pilot;
- public cost, SKU, stock balance, exact availability, supplier, branch-private, staff,
  document, payment-reference, or performance data;
- official tax invoices, fiscal receipts, electronic invoicing, VAT filing, statutory
  accounting, payroll, or legal certification;
- unrestricted production launch.

## Decisions

### Decision 1: use responsive cards, not a literal fixed table

Catalog products appear as semantic card lists arranged in rows and columns on wider
screens. They reflow to fewer columns on phones rather than forcing horizontal scrolling.

Each card may show:

- primary image or a neutral fallback;
- product name;
- category;
- bounded description;
- active size and color labels;
- stock unit;
- selling price or price range where the current viewer is permitted to see it;
- active/inactive or public/private state where relevant; and
- authorized actions.

Cards must preserve logical reading order, headings, labels, keyboard operation, and
screen-reader text. Dense variant management may retain an accessible table or list below a
product card when that representation is clearer than forcing every variant value into a
visual tile.

### Decision 2: add one optional primary image per product

The pilot supports one current image at product level. The same image may be used in the
internal catalog, online sale picker, and public storefront when the product is published.
Images are optional so existing products and text-only workflows continue to work.

Variant-specific galleries, multiple angles, video, and image ordering remain excluded.

### Decision 3: product media is sanitized presentation data

Only authorized owner or manager memberships may add, replace, or remove a product image.
The server must:

- accept only bounded JPEG, PNG, or WebP uploads whose signatures and decoded content agree;
- reject SVG, GIF, archives, executables, malformed images, excessive dimensions,
  decompression bombs, and files above the configured byte limit;
- decode and re-encode the image into an approved output format;
- apply orientation safely;
- strip EXIF and other embedded metadata;
- create bounded display and thumbnail derivatives;
- use opaque generated storage keys without the original filename;
- record hashes, dimensions, actor, event type, and timestamps without logging image bytes;
  and
- remove or reconcile replaced objects through an auditable service and maintenance command.

Raw uploaded bytes are not retained after successful sanitization. Product-media storage is
separate from static application assets and private source documents.

### Decision 4: public image access follows publication state

Internal image delivery requires an authenticated active membership in the same business.
Anonymous image delivery succeeds only when:

- the public profile is published and not suspended;
- the product is active and publicly selected; and
- the current sanitized image belongs to that product and business.

Unpublished, suspended, removed, unknown, and cross-tenant image requests return the same
generic not-found behavior. Public responses expose no storage key, original filename, user,
business ID, or private metadata. Removal from Ife is immediate, while previously cached
copies outside Ife may persist and must be disclosed to owners.

### Decision 5: cost remains private

The internal manager catalog may show each variant's reference cost only when
`can_view_inventory_cost` is true. The cashier sale picker shows selling price but never
reference cost, moving-average cost, assigned inventory cost, supplier information, or
performance values.

The public catalog never shows any form of cost. Existing owner-controlled public selling
price visibility remains authoritative.

### Decision 6: visual sale selection enhances, but does not replace, the safe form

Normal online sale-draft entry gains a searchable card picker showing image, product,
size/color, stock unit, and selling price. Selecting a card fills the existing variant field
and quantity line.

The underlying server-rendered formset remains the submitted source, works without
JavaScript, and is revalidated by the existing sale-draft service. The picker cannot submit
cost, price overrides, stock claims, business IDs, or branch authority.

The Stage 9A offline catalog remains text-first in this stage. Offline image caching would
expand local-storage and shared-device risk and requires a separate approval.

### Decision 7: Telebirr uses a branch merchant QR with manual evidence

Each branch may have one active Telebirr merchant QR profile containing:

- direct business and branch scope;
- merchant display name;
- a bounded merchant identifier or label suitable for operator confirmation;
- one sanitized QR image;
- active/inactive state;
- owner who confirmed it;
- confirmation time; and
- immutable configuration events.

Only the owner may create, replace, activate, deactivate, or remove the payment QR. Managers
and assigned cashiers may view it for an authorized branch.

The system does not claim that an uploaded QR belongs to Telebirr or to the named merchant.
The owner is responsible for obtaining it from the merchant account and confirming that a
test scan displays the expected merchant identity before activation.

The stored QR derivative must use a lossless format and preserve sufficient contrast,
quiet-zone space, and resolution for reliable scanning. Product-photo compression rules
must not be reused in a way that blurs or crops payment QR modules.

### Decision 8: QR display never means payment confirmation

When Telebirr is selected on the sale-posting page, the interface shows:

- exact sale amount in ETB;
- branch merchant display name and identifier;
- active merchant QR;
- a warning to verify the merchant identity in the customer's Telebirr app; and
- the existing required transaction-reference field.

The cashier must observe the customer's successful result and manually enter the transaction
reference. Existing normalization, business-scoped uniqueness, atomic posting, receipt, and
no-cash-movement rules remain unchanged.

The interface and internal receipt must continue to say that the reference is manually
observed operational evidence and is not provider verification or settlement confirmation.
No sale may post as Telebirr merely because the QR was displayed or scanned.

### Decision 9: missing QR does not weaken payment validation

If no active QR is configured, Telebirr may remain available only under the existing manual
reference procedure approved in Stage 3A. The UI clearly states that no branch QR is
configured.

An invalid, unavailable, or removed QR cannot bypass the required reference or change an
existing posted payment. Posted sales remain immutable; corrections continue through return,
refund, and reversal evidence.

### Decision 10: payment QR is not public checkout

The merchant QR is shown only in authenticated branch sale-posting and authorized printable
in-store material. It is not placed on the read-only public storefront because Stage 7 has
no order, amount, cart, payment-intent, fulfillment, or customer-support workflow.

Public payment or ordering requires a separately approved stage.

### Decision 11: staff-entry QR contains only an HTTPS URL

An authorized printable staff poster may encode the configured production login URL. A
phone camera opens the normal browser login page; no app-store installation is required.

The QR must never contain a username, password, session, business ID, branch authorization,
reset token, secret, or bypass. Authentication and active-membership checks remain mandatory.
The poster states that it is a login shortcut, not a credential.

### Decision 12: browser use is the supported mobile delivery model

The pilot supports the responsive web application in current:

- Android Chrome;
- iPhone Safari;
- desktop Chrome;
- desktop Firefox; and
- desktop Edge.

Automated responsive checks do not substitute for real Android and iPhone acceptance.
PWA installation remains optional. Core online workflows must work in the browser without
installation. Known iOS service-worker and storage limitations must be documented rather
than hidden.

### Decision 13: language selection is explicit

The application adds an accessible language control for English, Amharic, and Afaan Oromoo.
It uses Django's supported language mechanism, preserves only safe same-origin return paths,
and exposes the active language through the document `lang` attribute.

Every user-facing source string, including JavaScript-provided labels and operational
warnings, must be extractable. Empty or machine-invented translations do not pass the gate.
Named native speakers must review terminology, financial warnings, Telebirr language,
offline disclaimers, public-indexing consent-shaped text, and destructive actions.

### Decision 14: accessibility targets WCAG 2.2 AA

Stage 12 remediates the complete pilot path for:

- keyboard-only use;
- visible focus;
- skip navigation and landmarks;
- accessible names, descriptions, errors, status, and alert announcements;
- heading and table structure;
- form grouping and required-field communication;
- color contrast and non-color status cues;
- 200% text zoom and 320 CSS-pixel reflow;
- touch targets suitable for mobile operation;
- reduced-motion preference;
- image alternative text;
- QR alternatives as visible text instructions and URLs; and
- print output legibility.

Automated checks support but do not replace keyboard, screen-reader, zoom, and human review.

### Decision 15: security controls fail closed

Stage 12 adds or verifies:

- a restrictive Content Security Policy without unsafe inline script execution;
- `Referrer-Policy`, `Permissions-Policy`, frame denial, MIME sniffing protection, HTTPS
  redirect, secure cookies, HSTS, explicit hosts, and trusted CSRF origins;
- bounded session lifetime and documented shared-device sign-out behavior;
- safe password-reset/account-recovery procedure for the controlled pilot;
- deployment-edge rate limits for login, password recovery, public reads, uploads, offline
  sync, and other sensitive writes;
- dependency and container scanning;
- tenant-isolation and horizontal-authorization regression coverage;
- upload abuse, malformed-image, QR replacement, and storage reconciliation tests;
- secret rotation and least-privilege deployment accounts;
- structured security logging without passwords, sessions, document bytes, payment
  references, or unnecessary personal data; and
- documented threat model, incident contacts, severity, containment, communication,
  rollback, and post-incident review.

Rate limiting must be effective across all application instances. A per-process development
counter is not production evidence.

### Decision 16: readiness is enforced by checks and evidence

`manage.py check --deploy` and a Stage 12 pilot-readiness command must report blocking
failures for unsafe production configuration, including missing HTTPS origin, unsafe
cookies/hosts, absent CSP, unapproved rate limiting, development document storage/scanner,
unapproved retention policy, unconfigured product-media storage, or missing backup/restore
attestation.

Operational attestations must be explicit configuration backed by dated human evidence.
Setting a Boolean without performing the required work is not acceptance.

### Decision 17: recovery covers database and stored objects together

Before pilot:

- approve a recovery point objective and recovery time objective;
- run encrypted automated PostgreSQL backups;
- protect private-document and product/payment-media objects in a separate failure domain;
- restrict and audit backup access;
- restore into an isolated environment;
- verify tenant counts, ledger invariants, receipt identities, public profiles, offline sync
  evidence, document hashes and links, product images, Telebirr configuration, and audit
  events; and
- record duration, point restored, integrity results, failures, and corrective action.

The recommended pilot targets are an RPO of at most one hour and an RTO of at most four
hours. The product owner and deployment operator must approve or replace those targets.

### Decision 18: the pilot is deliberately bounded

The first pilot uses:

- one approved business;
- one active operating branch;
- named owner, manager, cashier, and stock roles;
- controlled devices and browser profiles;
- documented opening, sales, Telebirr, returns, cash close, purchasing, receiving, expenses,
  stock count, documents, reporting, public profile, offline outage, backup, restore, and
  incident scenarios;
- daily reconciliation and issue review; and
- a manual fallback and rollback procedure.

Multi-branch transfers are not available, so the pilot must not rely on moving stock between
branches inside Ife.

### Decision 19: existing human and production gates remain real

Stage 12 cannot mark the product pilot-ready until:

- an accountant approves or explicitly qualifies the Stage 5 formulas and exclusions;
- native speakers approve Amharic and Afaan Oromoo;
- Stage 8 uses production private object storage and a healthy production scanner;
- document retention, privacy, legal, deletion, export, and backup policy is approved;
- HTTPS, CSP, cross-instance rate limiting, monitoring, and incident response are live;
- restore evidence passes;
- provisional offline-note policy and operator training are approved;
- mobile/browser and accessibility acceptance passes; and
- independent Claude, security/privacy, and product-owner reviews are complete.

## Data model implications

The implementation should add bounded business-owned records such as:

### Product image

- direct `business`;
- protected `product`;
- opaque storage key;
- content hash;
- output media type;
- width and height;
- byte size;
- alternative text;
- actor and timestamps.

Only one current image may exist per product. Writes go through a typed catalog-media
service. Direct bulk writes are prohibited.

### Product image event

- direct `business`;
- protected `product`;
- optional current image;
- actor;
- action: added, replaced, removed, or reconciled;
- prior and resulting hashes where applicable;
- timestamp.

Events retain evidence without retaining unsafe raw uploads.

### Branch Telebirr profile

- direct `business`;
- protected `branch`;
- merchant display name and bounded identifier;
- sanitized QR storage key and hash;
- active state;
- owner confirmation actor and timestamp;
- created and updated timestamps.

One profile exists per branch. Changes go through an owner-only service and create immutable
events.

### Branch Telebirr event

- direct `business` and `branch`;
- protected profile;
- actor;
- action: created, replaced, activated, deactivated, or removed;
- bounded before/after metadata without QR bytes or payment secrets;
- timestamp.

These models contain no Telebirr credential, API key, customer phone number, provider token,
settlement status, or webhook data.

## Service and storage design

Catalog-media and Telebirr-QR writes must:

1. derive business and authorization from the active membership;
2. validate product or branch tenant scope;
3. validate upload byte and pixel limits before persistent publication;
4. decode, normalize, strip metadata, and re-encode;
5. write an opaque object;
6. validate model constraints;
7. atomically switch the current reference and append an immutable event;
8. remove a newly written object if the database transaction fails;
9. schedule or safely perform old-object cleanup after commit; and
10. expose reconciliation and dry-run cleanup commands.

Public delivery revalidates current publication state on every request. Internal delivery
revalidates active membership and business scope.

## UI workflow

### Add a product image

1. Owner or manager opens the product.
2. They upload one supported image and provide concise alternative text.
3. The server validates and sanitizes it.
4. The preview shows the exact stored derivative.
5. Replacement or removal requires confirmation and records an event.

### Use the internal catalog

1. User opens Products.
2. Responsive cards show images and active size/color choices.
3. Owners/managers see authorized catalog controls and cost.
4. Cashiers see selling information without cost.
5. Keyboard and text-list fallback remain available.

### Create an online sale draft

1. Cashier opens New sale.
2. Searchable cards narrow by product, size, color, or SKU label.
3. Selecting a card populates the existing variant field.
4. Cashier enters quantity and saves an ordinary draft.
5. Server validation remains unchanged.

### Publish a product

1. Owner/manager selects product visibility and optional public selling prices.
2. Preview shows image, description, variants, and permitted prices.
3. Owner publishes the profile.
4. Anonymous visitors see only the approved projection.

### Configure Telebirr QR

1. Owner chooses a branch and uploads the official merchant QR image.
2. Owner enters the merchant display name and identifier shown during payment.
3. Owner performs a real test scan outside Ife and confirms the expected merchant.
4. Activation records the owner's confirmation.
5. Replacement or deactivation is explicit and audited.

### Post a Telebirr sale

1. Cashier opens a sale draft and chooses Telebirr.
2. Page displays exact ETB amount, branch merchant identity, QR, and warning.
3. Customer scans using the Telebirr app.
4. Cashier observes the customer's successful result.
5. Cashier enters the transaction reference.
6. Existing posting service validates uniqueness and posts atomically.
7. Receipt states that the reference is manually observed, not provider verified.

## Acceptance tests

Automated and human verification should cover at least:

1. product images accept valid JPEG, PNG, and WebP and reject mismatched signatures;
2. malformed, oversized, excessive-pixel, animated, SVG, and decompression-bomb inputs fail
   safely;
3. stored output is re-encoded, bounded, and stripped of original metadata;
4. image create, replacement, removal, rollback, orphan reconciliation, and dry-run cleanup;
5. cross-business and unauthorized media access is denied;
6. unpublished/suspended public images return generic not-found;
7. cashier never sees reference or inventory cost in cards, HTML, JSON, or image metadata;
8. public pages never expose cost, SKU, stock, exact availability, supplier, branch-private,
   staff, document, or payment-reference data;
9. card grids reflow on phones and remain keyboard/screen-reader usable;
10. native select/formset fallback creates the same sale draft without JavaScript;
11. visual picker cannot override price, cost, tenant, branch, or posting state;
12. only owner can configure or activate a branch Telebirr QR;
13. cashier sees only an authorized branch QR;
14. QR display alone cannot post a sale;
15. Telebirr posting still requires a unique normalized manual reference;
16. cash posting cannot preserve a Telebirr reference;
17. Telebirr creates no physical-cash movement;
18. posted payment and receipt remain immutable;
19. staff-entry QR contains only the configured HTTPS login URL;
20. unsafe or missing production origin fails closed;
21. language switch works with safe same-origin return paths;
22. translation catalogs contain every source string and reviewed translations;
23. keyboard, focus, zoom, reflow, contrast, screen-reader, touch-target, and print checks;
24. Android Chrome, iPhone Safari, Chrome, Firefox, and Edge golden paths;
25. PWA remains optional and browser-only operation works;
26. CSP blocks unexpected scripts, frames, objects, and cross-origin form submission;
27. login, public, upload, and sync rate limits work across production instances;
28. session expiry and sign-out do not expose another cashier's offline queue;
29. dependency, container, tenant-isolation, and upload-abuse security checks;
30. production system checks fail when required controls are absent;
31. isolated database/object restore meets approved RPO/RTO and passes integrity checks;
32. full pilot scenarios reconcile inventory, cash, payments, receipts, returns, expenses,
   stock counts, performance, documents, public views, and offline evidence; and
33. existing SQLite and PostgreSQL regression suites remain green.

## Pilot evidence package

The final review package must include:

- approved Stage 12 brief;
- migration and architecture summary;
- privacy and threat-model summary;
- role/field exposure matrix;
- image and QR storage/reconciliation evidence;
- security-header and CSP evidence;
- rate-limit and abuse-test evidence;
- translation reviewer names, dates, and accepted catalogs;
- accessibility report and unresolved exceptions;
- real-device/browser matrix;
- backup and isolated restore report;
- accountant decision for Stage 5;
- Stage 8 storage/scanner/retention evidence;
- operator training and provisional-note approval;
- pilot runbook, support contacts, rollback plan, and go/no-go checklist;
- SQLite and PostgreSQL results;
- browser recordings or screenshots for the approved golden paths; and
- independent Claude findings and remediation record.

## Owner approval decision

Approve this recommended complete Stage 12 scope as written, request changes, or defer it.

Approval authorizes one bounded final pre-pilot stage: optional product images and responsive
catalog cards, manual-reference Telebirr merchant QR, staff browser-entry QR, localization,
accessibility, security, recovery, and controlled pilot readiness.

Approval does not authorize provider API integration, public checkout, customer/loyalty
features, multi-branch transfers, SaaS billing/onboarding, official tax/e-invoice behavior,
native apps, or unrestricted production launch.
