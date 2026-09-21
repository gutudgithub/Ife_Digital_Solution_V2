# Stage 8 feature brief: document capture and human-confirmed digitization

## Status

Approved by the product owner on 20 September 2026 and implemented for independent review.
The product owner's earlier D7 decision also approved the direction: private image capture,
structured human transcription, and mandatory owner confirmation before posting.

Independent Claude review and the production gates in this brief remain required before
acceptance or real-document use.

Stage 6 customer, loyalty, promotion, points-ledger, and consent work remains deferred.
Stage 5 accountant review and the Stage 7 privacy, security, native-language, and deployment
release gates remain outstanding and are not silently resolved by Stage 8.

## Objective

Stage 8 lets an owner or manager preserve a private source document, transcribe its
operational facts into a structured draft, compare the source with the draft, and require an
owner to confirm that transcription before any operational posting can occur.

The complete bounded stage provides:

- online browser upload of private source documents, with a mobile camera hint where the
  browser supports it;
- private multi-file document capture for JPEG, PNG, and PDF sources;
- file-size, file-signature, malware-scan, storage, and tenant-access controls;
- exact-file duplicate detection using cryptographic hashes;
- typed human transcription for a supplier purchase, an operating expense, or opening
  stock;
- immutable revision evidence for every material transcription change;
- side-by-side image review and secure PDF download;
- an explicit ready-for-confirmation state;
- owner-only confirmation that freezes the transcription and creates an ordinary
  operational draft;
- separate posting through the existing purchase, expense, or inventory service boundary;
- durable source-to-transcription-to-operational-record links;
- idempotent confirmation and opening-stock posting;
- cancellation, quarantine, retry, reconciliation, and recovery procedures;
- owner/manager document inbox, review, traceability, and history interfaces; and
- tenant-isolation, authorization, upload-security, concurrency, traceability,
  accessibility, localization, backup, and restore tests.

Stage 8 is not OCR. It does not read printed or handwritten text automatically, infer
products, create suppliers, calculate accounting classifications, or post financial or
inventory effects from an uploaded file.

## Architecture findings

The existing modular Django monolith already provides:

- server-derived active business membership and strict tenant-scoped views;
- owner and manager permissions for purchasing, expenses, and inventory management;
- draft purchases and purchase lines with separate approval and receiving;
- draft operating expenses with separate idempotent posting and reversal;
- idempotent opening-stock posting through the inventory service;
- immutable inventory, expense, cash, and purchasing evidence after posting;
- fixed-precision `Decimal` quantities, costs, and money;
- PostgreSQL transaction and locking patterns for operational posting;
- server-rendered, translation-ready forms and templates; and
- no existing public or authenticated file-upload surface.

At proposal time, the code did not provide:

- private durable document storage;
- upload validation or malware scanning;
- source-document, page/file, transcription, revision, or provenance models;
- a document-review inbox;
- owner confirmation before materializing an operational draft;
- a typed multi-line opening-stock draft;
- source-document links on purchases, expenses, or stock operations;
- document retention, reconciliation, export, or secure deletion procedures; or
- backup and restore coverage for stored source files.

Stage 8 therefore uses a separate `documents` module. It calls existing typed
services, but it must not write posted purchasing, expense, cash, or inventory evidence
directly.

## Required product, privacy, and security decisions

### Decision 1: preserve the approved non-AI boundary

Recommended:

- all transcription is entered by an authenticated human;
- no OCR, handwriting recognition, image classification, document classification,
  generative model, hosted extraction API, or automatic line-item extraction is included;
- no values are suggested from the source file;
- no source file is sent to an external recognition provider;
- deterministic QR or barcode reading is also deferred because it is not required for the
  Stage 8 traceability exit condition; and
- any later OCR proposal requires a separate approved brief, provider/privacy review, error
  policy, cost limit, and proof that extracted values remain unposted drafts.

This preserves the owner's explicit exclusion of AI and the approved D7 decision.

### Decision 2: support three typed workflows only

Recommended Stage 8 document workflows:

1. **Supplier purchase document**
   - source examples: supplier invoice, supplier receipt, purchase note;
   - transcription fields: branch, existing supplier, purchase date, optional supplier
     reference, optional expected date and settlement terms, existing variants, quantities,
     and unit costs;
   - owner confirmation creates one ordinary draft `Purchase` and its lines;
   - purchase approval and actual receiving remain separate existing actions; and
   - the source document does not prove that goods were physically received.
2. **Operating-expense document**
   - source examples: rent receipt, utility receipt, transport receipt, paid service receipt;
   - transcription fields: branch, existing expense category, business date, payee,
     description, amount, cash or manually referenced Telebirr payment details;
   - owner confirmation creates one ordinary draft `OperatingExpense`; and
   - expense posting, cash-session effects, reversal, and settlement rules remain unchanged.
3. **Opening-stock document**
   - source examples: verified opening-stock sheet or one notebook page used only to establish
     opening quantities;
   - transcription fields: branch, existing variants, quantities, and evidenced unit costs;
   - owner confirmation freezes one typed opening-stock batch;
   - a separate owner/manager action posts every valid line atomically through
     `post_opening_balance`; and
   - every variant must still have no prior inventory movement.

A notebook page is not a historical-sales import. Stage 8 does not reconstruct old sales,
cash, customer, supplier-settlement, return, stock-count, attendance, or performance events.

Purchase receiving documents, purchase returns, sales, sale returns, cash closures, supplier
settlements, stock counts, and attendance may receive document support only in a later
approved extension.

### Decision 3: use online browser capture without adding the offline PWA

Recommended:

- authenticated users may select existing files through the browser;
- image inputs may include `capture="environment"` as a best-effort mobile camera hint;
- upload, scanning, storage, and confirmation require a live server connection;
- interrupted uploads fail visibly and can be retried without creating a transcription;
- Stage 8 stores no document or draft in browser local storage or IndexedDB;
- no native mobile application, background upload, cropper, image editor, or camera SDK is
  added; and
- offline capture and synchronization remain Stage 9.

Browser camera behavior varies by device. File selection remains the supported fallback.

### Decision 4: accept a bounded private file set

Recommended upload limits:

- one captured document may contain one to five files;
- each file may be JPEG, PNG, or PDF;
- each file is at most 10 MiB and the complete document is at most 25 MiB;
- a PDF may contain multiple pages;
- filenames are retained only as private display metadata and never used as storage keys;
- generated UUID storage keys prevent path traversal and name collisions;
- MIME type is determined from validated file signatures, not the browser-provided value;
- SVG, HTML, office documents, archives, executables, audio, video, HEIC, and unknown
  formats are rejected with a translated error; and
- files cannot be replaced in place. A wrong upload is cancelled and captured again.

The original accepted bytes are retained as immutable evidence. Stage 8 does not claim that
an uploaded image is authentic, legally valid, or unaltered before upload.

### Decision 5: require quarantine and fail-closed malware scanning

Recommended:

- every new file starts in quarantine and cannot be previewed, downloaded, transcribed, or
  confirmed;
- basic signature and size validation occurs before durable acceptance;
- a configured malware-scanner adapter scans the exact bytes;
- a clean result makes the document available;
- a malicious result rejects the document and schedules the bytes for secure removal;
- scanner errors retain quarantine and show a retryable operational error;
- scanner timeouts never mark a file clean;
- production configuration fails deployment checks when no scanner is configured;
- tests use a deterministic fake scanner, not a claim of real malware coverage; and
- logs record document IDs and scan outcomes without original filenames or document text.

The implementation supports a ClamAV-compatible scanner without introducing a
background queue. A bounded synchronous scan is acceptable for the Stage 8 size limits.
Production must validate scanner availability, timeout behavior, signature updates, and
alerting before real documents are accepted.

### Decision 6: use private object storage in production

Recommended:

- local development and tests use a non-public local document directory;
- staging and production use a dedicated private S3-compatible bucket or approved equivalent;
- source objects are encrypted in transit and at rest using the storage provider's supported
  controls;
- buckets deny anonymous listing and object access;
- application storage keys are generated and never contain tenant names, supplier names,
  filenames, or document text;
- source objects are never placed under WhiteNoise, static, or Stage 7 public-media paths;
- access occurs only after current membership and role authorization;
- short-lived signed object URLs may be used only after authorization and only when the
  backend cannot safely stream the file;
- local file URLs and permanent public object URLs are prohibited; and
- storage credentials use least privilege for the dedicated private prefix.

Production object-storage provisioning is an infrastructure release gate. The application
must fail closed rather than silently store production documents on an ephemeral container
filesystem.

### Decision 7: preserve immutable source and transcription history

Recommended source lifecycle:

```text
uploading -> quarantined -> available
                         -> rejected
available -> cancelled
```

Recommended transcription lifecycle:

```text
draft -> awaiting_owner_confirmation -> confirmed
  |                 |
  +---- cancelled <-+
```

Rules:

- a source file is never overwritten;
- reordering, adding, or removing files is allowed only before transcription work starts and
  is recorded as immutable revision evidence;
- every material transcription edit records an immutable before/after snapshot, actor, and
  timestamp;
- submitting for confirmation freezes manager editing;
- an owner may return a task to draft with a required reason;
- owner confirmation freezes the source set and transcription permanently;
- a source-derived purchase or expense draft cannot be edited after confirmation;
- correction before posting cancels the source-derived target and starts a replacement
  transcription against the same captured source, preserving the first attempt;
- correction after posting uses the target workflow's normal reversal rules;
- cancellation never erases audit metadata; and
- posted or reversed target records do not mutate the captured source or confirmation.

The system must not call a mutable draft "digitized and posted" merely because it was
transcribed or confirmed.

### Decision 8: owners confirm; owners and managers may prepare

Recommended authorization:

- owner: upload, view, download, transcribe, submit, return, confirm, cancel, materialize,
  post a confirmed opening-stock batch, and view traceability;
- manager: upload, view, download, transcribe, submit, cancel an unconfirmed task, and use the
  resulting operational draft within existing permissions;
- cashier: no Stage 8 document access;
- stock employee: no Stage 8 document access;
- unrelated business members: no access; and
- platform staff: no document access merely because `is_staff` is true.

Only an active owner membership may perform the D7 confirmation. The same owner may also
have entered the transcription; Stage 8 does not impose a two-person or high-value approval
rule.

Every route derives business scope from the active membership. A submitted business ID,
branch ID, supplier ID, category ID, variant ID, document ID, or target ID is always
revalidated against that business and the actor's permission.

### Decision 9: confirmation creates drafts, not ledger effects

Recommended:

- confirmation validates the complete typed transcription inside one database transaction;
- confirmation uses a stable idempotency key stored on the transcription;
- an exact replay returns the same result;
- a conflicting replay fails closed;
- purchase confirmation creates one draft purchase and draft lines;
- expense confirmation creates one draft expense;
- the source-derived purchase or expense draft is locked against ordinary edits so the
  eventual posted values cannot differ from the owner's confirmed snapshot;
- opening-stock confirmation creates no inventory movement;
- purchase approval, receiving, expense posting, and opening-stock posting remain explicit
  later actions;
- ordinary manual purchase, expense, and opening-balance workflows remain available without
  requiring a source document; and
- existing posting services remain authoritative for permissions, cash-session rules,
  inventory locks, validation, idempotency, and audit evidence.

Stage 8 does not make source documents mandatory for all operational records. A later pilot
policy may require attachments for selected categories only after workflow evidence supports
that restriction.

### Decision 10: use explicit, navigable provenance links

Recommended trace paths:

```text
captured document
  -> ordered source files and hashes
  -> immutable transcription revisions
  -> owner-confirmed snapshot
  -> operational draft
  -> existing posted record and ledger evidence
  -> existing reversal or cancellation evidence, when applicable
```

Requirements:

- the document workspace links to the created purchase, expense, or opening-stock operations;
- target detail pages show a private source-document link only to authorized owners/managers;
- purchase traceability continues through purchase lines, receiving progress, and inventory
  movements;
- expense traceability continues through payment, cash movement where applicable, and
  reversal;
- opening-stock lines link directly to their `StockOperation` and inventory movement;
- confirmed source metadata and hashes remain readable even if a retention process later
  removes the bytes;
- Stage 7 public pages, QR routes, sitemaps, receipt verification, and exports never expose a
  document link, filename, hash, transcription, storage key, or signed URL; and
- reports continue to derive from posted operational evidence, never unconfirmed documents.

### Decision 11: use exact-hash duplicate detection without fuzzy claims

Recommended:

- calculate SHA-256 for every accepted file while reading its bytes;
- calculate an ordered document fingerprint from the file hashes;
- enforce one active captured document per business and exact fingerprint;
- a repeated upload returns the existing document rather than storing duplicate bytes;
- concurrent duplicate uploads are serialized by a database uniqueness constraint;
- a cancelled unconfirmed duplicate may be restored or intentionally recaptured through an
  explicit service;
- one captured source may retain multiple sequential transcription attempts, but only one
  unconfirmed attempt may be active at a time;
- the system may warn when a nonblank supplier reference already exists for the same
  supplier, but it does not automatically declare fraud or duplication; and
- no visual-similarity, fuzzy-text, handwriting, or semantic duplicate detector is included.

Hashes are integrity and exact-duplicate evidence, not proof of who created the physical
document.

### Decision 12: define bounded retention and deletion behavior

Recommended initial behavior:

- rejected bytes are removed after scanner and incident evidence requirements are satisfied;
- cancelled, unconfirmed documents are eligible for file purge after 30 days;
- confirmed document bytes are not deletable through normal application screens;
- database metadata, hashes, revisions, confirmation, target links, and purge evidence remain
  immutable after a file purge;
- a scheduled command identifies eligible unconfirmed files and performs bounded cleanup;
- tenant offboarding, legal retention, data-subject requests, litigation holds, and confirmed
  financial-document retention require a separately approved production policy;
- Stage 8 does not promise immediate erasure of backup copies; and
- production use with real documents remains blocked until the product owner and qualified
  privacy/legal reviewer approve retention periods, lawful basis, processor terms, and
  deletion/export procedures.

The default protects evidence but is not a legal-retention conclusion.

### Decision 13: keep source access private and auditable

Recommended:

- owners and managers may preview clean JPEG/PNG files;
- PDFs download through an authorized endpoint by default rather than rendering inside the
  application origin;
- responses use `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`, and a
  safe `Content-Disposition`;
- source responses never permit range or cache behavior that bypasses current authorization
  without an explicit tested implementation;
- each preview or download records document, file, actor, action, and timestamp without IP
  address, device fingerprint, or document content;
- no source content appears in application logs, error tracking, analytics, email, or public
  links; and
- a suspected document leak uses the incident-response process and rotates any affected
  storage credentials or signed-link configuration.

### Decision 14: fail visibly and recover without partial operational records

Recommended:

- upload failure creates no usable source document;
- object-storage success followed by database failure produces a detectable orphan that a
  reconciliation command can remove after the configured safety window, without racing an
  in-flight upload;
- database success followed by missing storage bytes leaves the document unavailable and
  raises an operational alert;
- scan failure stays quarantined and can be retried idempotently;
- confirmation failure creates no purchase, expense, opening-stock operation, or partial
  target lines;
- opening-stock batch posting is all-or-nothing across every line;
- one invalid or no-longer-eligible opening variant blocks the complete batch and identifies
  the line;
- repeated confirmation or posting does not duplicate target records or inventory movement;
- a document-storage reconciliation command reports missing objects, orphan objects,
  unexpected hashes, stale quarantine, and incomplete target links; and
- no recovery command silently posts business transactions.

## Proposed data model

Every Stage 8 business-owned row carries direct `business_id`. Operational document and line
rows also carry direct `branch_id` where applicable.

### `CapturedDocument`

Recommended fields:

- UUID primary key;
- business and branch;
- document kind: supplier purchase, operating expense, opening stock;
- lifecycle status;
- ordered-document fingerprint;
- private title or short operator label;
- uploaded by and uploaded at;
- cancelled by, cancelled at, and required cancellation reason;
- available/rejected timestamps; and
- created and updated timestamps.

The title is private convenience metadata, not a supplier reference or legal-document claim.

### `CapturedDocumentFile`

Recommended fields:

- UUID primary key;
- business and captured document;
- one-based page/file position;
- opaque storage key;
- private original filename;
- validated media type;
- byte size;
- SHA-256;
- scan state and scanner result code;
- scanned at;
- storage-created timestamp;
- purged at and purge reason; and
- immutable creation metadata.

Constraints prevent duplicate positions, invalid media types, nonpositive sizes, and
cross-business parent links. Storage keys and hashes cannot be changed after creation.

### `DocumentTranscription`

Recommended fields:

- UUID primary key;
- business, branch, and captured document;
- workflow type and lifecycle status;
- stable confirmation idempotency key;
- workflow-specific nullable typed header fields;
- optional linked draft purchase;
- optional linked draft operating expense;
- confirmed by and confirmed at;
- submitted by and submitted at;
- returned by, returned at, and required return reason;
- cancelled by, cancelled at, and required cancellation reason; and
- created and updated timestamps.

Database and model constraints require exactly the fields and target link permitted by the
selected workflow. A captured document may retain multiple sequential attempts, but a
partial uniqueness constraint permits only one `draft` or `awaiting_owner_confirmation`
transcription at a time.

Money uses two-decimal fields where the existing expense workflow does. Purchase and
opening-stock unit cost preserves the existing six-decimal inventory-cost precision.

### `DocumentTranscriptionLine`

Used by purchase and opening-stock workflows:

- UUID primary key;
- business, branch, and transcription;
- stable line order;
- existing product variant;
- quantity with stock-unit validation;
- six-decimal unit cost;
- optional private line note;
- stable per-line posting idempotency key for opening stock; and
- optional linked opening-stock `StockOperation`.

The business, branch, product, stock unit, and target operation must remain tenant-consistent.
Expense transcriptions cannot have lines.

### `DocumentTranscriptionRevision`

Immutable fields:

- business and transcription;
- monotonic revision number;
- action type;
- canonical before and after snapshots using strings for decimal and date values;
- actor and timestamp; and
- optional reason.

Snapshots contain structured fields only, never source bytes, signed URLs, storage
credentials, or unrestricted HTML.

### `DocumentAccessEvent`

Immutable fields:

- business, document, and file;
- preview or download action;
- actor and timestamp.

Access events are operational security evidence and are not exposed through Stage 7 or
performance reports.

## Service boundaries

All Stage 8 writes use typed services. Views and admin actions do not transition lifecycle
state or create operational targets directly.

Implemented services:

- `capture_document`
- `scan_document_files`
- `save_purchase_transcription`
- `save_expense_transcription`
- `save_opening_stock_transcription`
- `submit_transcription_for_confirmation`
- `return_transcription_to_draft`
- `confirm_transcription`
- `start_replacement_transcription`
- `post_confirmed_opening_stock`
- `cancel_transcription`
- `cancel_document`
- `record_document_access`
- `purge_eligible_document_files`
- `reconcile_document_storage`

`capture_document` validates the complete one-to-five-file intake, creates its pending
metadata and opaque objects, and invokes the quarantine scan. `scan_document_files` is also
the retry boundary, so partially assembled upload records are not exposed as a separate
application workflow.

Confirmation and opening-stock posting use `transaction.atomic`, row locks, stored
idempotency keys, tenant revalidation, and existing target services. Object-storage actions
that cannot participate in a database transaction must use explicit pending states and
reconciliation rather than pretending to be atomic.

Operational model hooks or signals must not perform hidden posting. Target creation is
explicit in the confirmation service, and ledger effects remain explicit in the existing
posting services.

Normal Django admin editing is disabled for source, file, transcription, revision, access,
confirmation, and provenance rows. Admin may provide read-only inspection only to explicitly
authorized support roles after a later support-access policy; `is_staff` alone is
insufficient.

## Management interfaces

### Document inbox

Owner and manager view:

- status-filtered document list;
- branch, workflow type, uploader, date, and status filters;
- quarantine and scan failures separated from transcription work;
- age of pending owner confirmation;
- exact-duplicate result links;
- created target status and traceability; and
- pagination with no document bytes in list responses.

### Capture page

The capture form:

- selects one supported workflow and branch;
- accepts one to five allowed files;
- shows per-file and total limits before submission;
- offers a best-effort mobile camera hint for images;
- clearly states that no OCR occurs;
- clearly states that the uploader must verify the source and enter every value; and
- does not allow a user-supplied business identifier or storage key.

### Transcription workspace

The workspace:

- shows one clean image beside typed fields at desktop widths;
- provides accessible next/previous file controls;
- provides a secure PDF download link;
- works as a single-column sequence on narrow screens;
- supports keyboard operation and visible focus;
- scopes supplier, category, and variant choices to the active business;
- shows quantity units and Decimal precision;
- validates line totals and required fields;
- shows immutable revision history;
- lets a manager submit for owner confirmation; and
- never embeds third-party viewers, scripts, fonts, analytics, or recognition tools.

### Owner confirmation

The owner page:

- displays every source file and the frozen structured snapshot;
- displays calculated totals without claiming accounting or tax certification;
- requires an explicit confirmation checkbox and fresh POST;
- explains that confirmation verifies transcription, not document authenticity;
- creates the target draft exactly once; and
- links immediately to the created draft and its normal next action.

The confirmation page explains that source-derived target values are locked. A correction
requires target cancellation and a replacement transcription with a new owner confirmation.

### Traceability page

Authorized owners/managers can navigate:

- source document and hashes;
- scan results;
- transcription revisions;
- owner confirmation;
- created operational draft;
- current target status;
- posted inventory, expense-payment, and cash evidence already authorized for that role; and
- target cancellation or reversal evidence.

Cashiers, stock employees, anonymous users, unrelated tenants, and ordinary platform staff
receive no document metadata or existence signal.

## Security and privacy requirements

Implementation must include:

- CSRF protection and POST-only mutation routes;
- `require_safe` on document list, detail, preview, download, and history routes;
- file-count, per-file-size, total-size, signature, type, and hash validation;
- private storage with opaque generated keys;
- fail-closed scanning and quarantine;
- no executable or active-content format;
- owner/manager authorization on every object response;
- tenant filtering before generic not-found behavior;
- no source filename or hash in public or unauthorized errors;
- safe response headers and no-store document responses;
- rate and capacity controls sufficient to prevent one tenant exhausting storage;
- structured logs without document content or private filenames;
- no document data in Stage 7 projections, sitemaps, QR codes, metrics, or receipt
  verification;
- no automatic transmission to an OCR, analytics, email, or support provider;
- direct business/branch scope and database constraints on every new model;
- immutable confirmed and access evidence;
- secret-managed object-storage and scanner credentials; and
- dependency, threat-model, tenant-isolation, and upload-abuse review before pilot use.

Uploaded sources may contain supplier, employee, customer, location, payment, or other
personal data even when Stage 8 does not request it. The UI must warn operators to capture
only documents needed for an approved business purpose and to avoid unrelated personal
pages.

## Acceptance criteria

1. An owner or manager can upload one to five allowed files for one supported workflow.
2. Invalid type, signature, size, count, or total size is rejected without a usable document.
3. Files remain inaccessible until every file has a clean scan result.
4. Scanner failure remains quarantined and can be retried without duplicate files.
5. Exact repeated upload in the same business resolves to the existing document.
6. The same file in another business neither reveals nor links to the first business.
7. Owners and managers can transcribe; cashiers and stock employees cannot access Stage 8.
8. Managers cannot confirm a transcription.
9. Every material edit creates immutable before/after revision evidence.
10. Submission freezes manager editing until an owner returns or confirms the task.
11. Purchase confirmation creates exactly one ordinary draft purchase with exact lines.
12. Expense confirmation creates exactly one ordinary draft expense with exact Decimal
    amount and payment evidence fields.
13. Opening-stock confirmation creates no inventory movement.
14. Confirmed opening stock posts every line atomically through the existing service.
15. If one opening-stock line is invalid, no line posts.
16. Concurrent confirmation creates one target draft.
17. Concurrent or repeated opening-stock posting creates one operation per line.
18. Source-derived purchase and expense drafts cannot be edited after owner confirmation.
19. Cancelling an unposted target and replacing its transcription preserves both attempts.
20. Purchase approval/receiving, expense posting, and opening-stock posting retain their
    existing permission, idempotency, cash, inventory, and concurrency rules.
21. The source document links to its target, and the authorized target links back.
22. Reversal or cancellation preserves the original source and confirmed snapshot.
23. Unconfirmed documents and transcriptions never affect inventory, cash, expenses,
    purchasing progress, performance reports, or public pages.
24. Authorized image preview and PDF download use private no-store responses.
25. Unauthorized, unrelated-tenant, inactive-member, anonymous, and ordinary-staff requests
    cannot distinguish document existence.
26. Stage 7 public profile, product, receipt, QR, robots, sitemap, and metrics tests continue
    to prove no private document exposure.
27. Cancelled unconfirmed files become purge-eligible; confirmed evidence is not normally
    deletable.
28. Storage reconciliation detects missing, orphaned, hash-mismatched, stale-quarantined,
    and incomplete-link records without posting transactions.
29. Database and object-storage restore evidence proves source-to-target links survive.
30. English workflows pass accessibility checks and every user-facing string is translation
    ready.
31. Amharic and Afaan Oromoo remain disabled for Stage 8 until native-speaker review.
32. Production deployment fails closed without private durable storage and a healthy scanner.

## Verification plan

### Model and service tests

- business/branch/parent consistency and lifecycle constraints;
- immutable file keys, hashes, scan outcomes, revisions, confirmations, and access events;
- allowed file signatures, limits, filenames, and generated keys;
- exact document fingerprint and duplicate races;
- scan clean, infected, timeout, unavailable, and retry behavior;
- workflow-specific required and forbidden fields;
- Decimal precision and stock-unit quantity validation;
- existing supplier/category/variant tenant and active-state validation;
- owner-only confirmation;
- confirmation replay, conflict, rollback, and concurrency;
- exact purchase and expense draft materialization;
- source-derived target edit rejection and replacement-transcription history;
- opening-stock all-or-nothing posting, replay, locking, and stale eligibility;
- revision ordering and snapshot stability;
- target cancellation/reversal traceability;
- purge eligibility and immutable tombstones; and
- storage reconciliation findings and no-posting guarantee.

### View and security tests

- anonymous, inactive-member, wrong-role, and cross-tenant denial;
- generic not-found behavior without existence leakage;
- POST rejection on every read-only route;
- CSRF and safe-method enforcement;
- file validation errors without raw storage or scanner internals;
- no-store, nosniff, content disposition, and authenticated file delivery;
- no source metadata in list pages for unauthorized roles;
- scoped supplier, category, branch, and variant choices;
- manager preparation and owner confirmation boundaries;
- accessible errors, field associations, focus, keyboard controls, and narrow-screen layout;
- Stage 7 public-data denylist regression; and
- upload capacity and request-boundary tests.

### Storage and operations tests

- local private storage in development;
- configured private object storage in staging;
- scanner health and signature-update evidence;
- storage/database failure reconciliation;
- backup and isolated restore of database plus objects;
- hash verification after restore;
- orphan and stale-quarantine cleanup;
- monitoring and alert exercise; and
- incident exercise for an incorrectly exposed signed URL or storage policy.

### Required repository checks

- focused Stage 8 tests on SQLite where supported;
- full PostgreSQL Stage 8 and affected purchasing, expense, inventory, performance, and
  public-profile suites;
- full test suite before independent review;
- Ruff lint and formatting checks;
- `mypy apps config`;
- Django system checks and deployment checks;
- migration drift check;
- pre-commit hooks; and
- browser testing of upload, quarantine result, transcription, owner confirmation,
  operational posting, traceability, duplicate handling, and unauthorized access.

## Documentation and operations

Implementation must update:

- product scope;
- architecture and authorization matrix;
- data model;
- business rules;
- security and privacy inventory;
- operations and incident response;
- owner/manager operating guide;
- demo data using synthetic source files only;
- environment examples for storage and scanner configuration;
- backup and restore procedures;
- deployment checks;
- roadmap status; and
- this brief's final status.

Operations must include:

- private-bucket policy checklist;
- scanner health, signature-update, retry, and outage procedure;
- storage quota and capacity alert;
- stale-quarantine procedure;
- orphan/missing/hash-mismatch reconciliation command and response;
- cancelled-document purge command and schedule;
- document leak response;
- tenant-offboarding hold pending approved retention policy;
- database and object-store coordinated backup;
- isolated restore with hash and provenance verification; and
- manual fallback: retain the physical document and use the ordinary manual operational
  workflow when capture is unavailable.

## Explicit exclusions

Stage 8 does not include:

- OCR, handwriting recognition, document classification, extraction suggestions, or AI;
- external document-processing APIs;
- automatic supplier, category, product, variant, or account creation;
- automatic accounting or tax classification;
- automatic posting, approval, receiving, payment, cash movement, or stock movement;
- official tax invoice, fiscal receipt, VAT, or electronic-invoice compliance;
- document authenticity, forgery detection, legal validation, or provider verification;
- historical sale, cash, return, attendance, or complete notebook migration;
- sales, sale-return, cash-session, stock-count, supplier-settlement, or attendance document
  workflows;
- mandatory documents for ordinary manual transactions;
- customer accounts, consent, loyalty, points, promotions, or public ordering;
- public uploads, public evidence submission, public document links, or public media;
- native mobile application, offline capture, synchronization, background upload, or device
  storage;
- image cropping, enhancement, compression, annotation, PDF editing, or format conversion;
- HEIC, office documents, spreadsheets, archives, audio, video, or active content;
- email, SMS, push, or external review notifications;
- two-person confirmation, value thresholds, or configurable approval chains;
- legal-retention conclusions, records-management certification, or guaranteed erasure from
  backups; or
- production use before storage, scanner, privacy, legal, security, backup/restore,
  monitoring, and native-language gates pass.

## Approval record

The recommended complete scope is:

1. preserve D7 as human transcription with no OCR or AI;
2. support supplier-purchase, operating-expense, and opening-stock documents only;
3. provide online file selection and a best-effort mobile camera hint without Stage 9
   offline behavior;
4. accept one to five private JPEG, PNG, or PDF files within strict limits;
5. quarantine every file and require a clean fail-closed malware scan;
6. use local private storage for development and private S3-compatible storage for
   staging/production;
7. preserve immutable source files, hashes, revisions, confirmation, access, and target
   links;
8. let owners and managers prepare documents while only owners confirm;
9. make confirmation create ordinary drafts with no ledger effect;
10. post only through existing purchasing, expense, and inventory services;
11. use exact-hash duplicate detection without fuzzy or authenticity claims;
12. purge cancelled unconfirmed files after 30 days while retaining immutable tombstones;
13. keep confirmed-source deletion and legal retention behind a production policy gate; and
14. retain every OCR, public-upload, offline, customer, regulatory, and expanded-document
    workflow outside Stage 8.

The product owner approved this complete bounded scope on 20 September 2026. The
implementation includes the private storage alias, signature validation, scan adapters,
typed services and interfaces, immutable revision/provenance evidence, existing-service
posting integration, retention/reconciliation commands, synthetic demo source, and automated
coverage.

Approval does not authorize production document processing, OCR, legal/tax claims, or any
excluded capability. Independent review and every stated production gate remain required.
