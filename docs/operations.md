# Operations

## Configuration

Runtime configuration is supplied through environment variables:

| Variable | Purpose |
| --- | --- |
| `DJANGO_DEBUG` | `true` only for local development |
| `DJANGO_SECRET_KEY` | unique high-entropy deployment secret |
| `DJANGO_ALLOWED_HOSTS` | comma-separated served hosts |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | comma-separated HTTPS origins |
| `POSTGRES_DB` | database name |
| `POSTGRES_USER` | database role |
| `POSTGRES_PASSWORD` | database credential |
| `POSTGRES_HOST` | database host; unset selects SQLite |
| `POSTGRES_PORT` | database port |
| `PUBLIC_SITE_ORIGIN` | canonical public HTTPS origin used in links and QR payloads |
| `PUBLIC_SUPPORT_URL` | optional HTTPS support link shown on public pages |
| `DOCUMENT_STORAGE_BACKEND` | `filesystem` for local development or `s3` for production |
| `DOCUMENT_S3_*` | private bucket, endpoint, region, and least-privilege credentials |
| `DOCUMENT_SCANNER_BACKEND` | deterministic local scanner or production ClamAV adapter |
| `DOCUMENT_SCANNER_HOST`, `DOCUMENT_SCANNER_PORT` | ClamAV-compatible scanner endpoint |
| `DOCUMENT_SCANNER_TIMEOUT_SECONDS` | bounded fail-closed scan timeout |
| `DOCUMENT_CONFIRMED_RETENTION_POLICY_APPROVED` | production retention-policy gate |
| `DOCUMENT_CANCELLED_RETENTION_DAYS` | unconfirmed cancelled-byte cleanup age |
| `DOCUMENT_QUARANTINE_STALE_HOURS` | threshold for stuck quarantine reconciliation |
| `CATALOG_MEDIA_STORAGE_BACKEND` | `filesystem` for development or private `s3` for production |
| `CATALOG_MEDIA_S3_*` | private product/Telebirr-media bucket configuration |
| `DJANGO_CACHE_BACKEND`, `DJANGO_CACHE_LOCATION` | shared production cache used by rate limits |
| `RATE_LIMIT_BACKEND_APPROVED` | records approval of the cross-instance rate-limit backend |
| `LOGIN_RATE_LIMIT`, `UPLOAD_RATE_LIMIT`, `SYNC_RATE_LIMIT` | bounded sensitive-write limits |
| `PUBLIC_READ_RATE_LIMIT` | bounded anonymous public-read limit |
| `STAGE12_RECOVERY_ATTESTATION_PATH` | protected JSON produced after an isolated restore drill |
| `STAGE12_*_APPROVED` | explicit security, privacy/legal, incident, training, and translation gates |

## Deployment sequence

1. Build an immutable application image.
2. Run quality and security checks.
3. Provision PostgreSQL, secrets, HTTPS, and durable backup storage.
4. Run migrations as a controlled one-off release step.
5. Start Gunicorn application instances as a non-root user.
6. Verify health, authentication, tenant isolation, static assets, and database access.
7. In an isolated staging tenant, smoke-test purchase approval, receiving, a purchase return,
   its full reversal, one cash sale, one manually referenced Telebirr sale, internal receipt
   printing, a partial customer return, a full sale reversal, a return reversal, refund
   evidence, one cash-session opening, linked cash sale/refund movements, a manual drawer
   movement, physical count, variance handling, closure report, authorized reopening, and
   movement/balance reconciliation. Also verify a draft and posted expense, one cash expense
   against an open session, one Telebirr expense, a partial supplier payment, a supplier-return
   credit/refund, exact-source reversals, settlement summaries, and cashier non-disclosure.
   Start a full-branch stock count, verify inventory posting is frozen, complete every blind
   line including explicit zeroes, submit and review quantity variances by stock unit, approve
   the exact-cost adjustments, print the internal evidence, reverse it, and reconcile every
   resulting movement and balance. As an owner and manager, run daily, Monday-weekly, and
   calendar-month performance reports across single and multiple authorized branches; compare
   KPI totals with time buckets, SKU rows, expense categories, and inventory balances; verify
   operational controls remain separate; download both CSV exports; and print the summary.
   Confirm cashier, stock-employee, inactive-member, unrelated-staff, and cross-business
   branch access is denied without cost or result leakage.
   As an owner, complete and publish a public profile, select products, hide one product's
   prices, print profile/product QR material, and immediately unpublish it. As a manager,
   verify draft editing is allowed but publication, indexing, and appeals are denied. Check
   generic not-found behavior for draft, unpublished, suspended, and unknown identifiers.
   Exercise dedicated verification and suspension permissions independently; verify public
   pages never expose SKU, cost, stock, branch, staff, supplier, receipt contents, Telebirr
   reference, or private verification evidence. Verify receipt tokens expose only the
   approved minimal fields.
   Capture a clean purchase source, transcribe it as manager, submit it, compare and confirm
   it as owner, then approve and receive the resulting normal purchase separately. Repeat
   with an expense and opening-stock source; verify confirmation has no ledger effect,
   opening-stock posting is atomic, replacement requires target cancellation, cashier/stock
   access is denied, and no private source data appears on public routes.
   While online, prepare the cashier offline-sales screen and catalog. Disconnect the test
   browser, create and retain a local draft, inspect the provisional non-receipt warning,
   reconnect, synchronize manually, and verify only a normal sale draft appears. Confirm no
   stock, cash, payment, receipt, or performance evidence exists until the ordinary online
   posting flow runs. Replay the same draft, exercise a price conflict and rejected draft,
   switch business/branch context to verify local queue isolation, then sign out and confirm
   browser storage/cache clearing is requested.
   Never use `seed_demo` in production.

## Offline-sales operations

- Serve production traffic over HTTPS. The service worker is scoped to `/offline/` and must
  not be broadened without a security review.
- Operators must visit the branch-specific offline-sales screen while connected before an
  outage. A missing or older-than-seven-days catalog cannot prepare new local drafts.
- Synchronization is operator-initiated. Do not add background sync, offline posting, or
  automatic conflict resolution as an operational workaround.
- Treat every provisional note as non-receipt local evidence. Train staff to complete the
  normal online posting flow after synchronization.
- Signing out clears browser caches and catalog snapshots, but preserves local sale drafts.
  The logout guard shows the pending/rejected draft count and requires confirmation. Queue
  rendering is membership-owned and requires a live browser-session marker; after sign-out,
  session expiry, browser restart, or membership change, reconnect and authenticate before
  viewing drafts.
- Seven days is the synchronization limit, not a deletion schedule. Expired attempts create
  immutable rejected sync evidence; local removal remains an explicit operator action.
- Review cash-session details for cash sales whose original sale date differs from the
  session business date.
- Before pilot, record whether the business accepts provisional notes during outages and
  complete native-language review of every warning and conflict message.

The development Compose command runs migrations automatically for convenience. Production
must not let every replica race to run migrations.

## Backup and restore

Before pilot use:

- define recovery point and recovery time objectives;
- take encrypted automated PostgreSQL backups;
- keep backups in a separate failure domain;
- restrict and audit backup access;
- perform scheduled restore drills into an isolated environment;
- record restore duration, integrity checks, and corrective actions.

A successful backup job is not recovery evidence; only a tested restore is.

Private-document recovery must restore PostgreSQL metadata and the private object bucket to a
consistent point. Restore drills must verify source hashes, scan state, transcription
revisions, confirmation evidence, access events, target links, and authorized download.
Database-only or bucket-only recovery is incomplete.

Use `record_recovery_attestation` only after the drill is complete. The protected output
records approver, evidence URL, database/object restore timestamps, and measured RPO/RTO.
Run `check_stage12_readiness` with the release configuration; it fails when the attestation
or another mandatory Stage 12 gate is absent.

## Observability

Production requires:

- structured application and audit logs without secrets or unnecessary personal data;
- request, error-rate, latency, database, capacity, and background-job metrics;
- alerts with an accountable responder and documented severity;
- uptime checks for application and dependency health;
- release identifiers that correlate errors with deployments.

## Incident and support readiness

Define owners, contact paths, severity levels, customer communication, data-breach
assessment, rollback procedures, and post-incident review. Pilot onboarding must include
support hours, training, feedback capture, and a safe path back to manual operations.
The complete controlled-pilot, fallback, incident, rollback, account-recovery, and go/no-go
procedure is in `docs/stage-12-pilot-runbook.md`.
Native-speaker review requirements and in-context scenarios are in
`docs/stage-12-translation-review.md`; untranslated catalog entries intentionally fall back to
English until that gate is complete.

## Catalog and Telebirr media operations

- Production must use private durable object storage with anonymous access denied,
  `querystring_auth=True`, and `default_acl=None`.
- Run `python manage.py reconcile_catalog_media` on the approved cadence. Missing referenced
  objects or hash mismatches are blocking integrity failures.
- Preview orphan cleanup with
  `python manage.py reconcile_catalog_media --purge-orphans`; apply only after investigation
  with `--apply`.
- Product uploads are decoded, orientation-normalized, metadata-stripped, resized, and stored
  as WebP. Telebirr QR uploads are contrast-checked and preserved as lossless PNG.
- The owner must test-scan a branch QR and verify merchant identity before activation.
  Activation records configuration approval, not payment or settlement verification.
- Replacement, deactivation, and removal retain immutable events. Never place QR bytes,
  payment references, provider credentials, or customer data in logs.

## Public storefront operations

- Configure `PUBLIC_SITE_ORIGIN` as the exact externally served HTTPS origin before issuing
  QR material. Unsafe or missing production configuration must fail closed.
- An owner can remove exposure immediately with **Public profile → Unpublish**. Search
  engines and third-party caches may retain earlier copies; use their removal processes
  separately when necessary.
- Authorized platform staff can suspend exposure independently of owner publication state.
  Reinstatement never republishes a profile the owner had unpublished.
- Verification correction uses a new owner request; authorized staff then approve, reject,
  renew, revoke, or expire the specific indicator. Never place documents, personal data, or
  secrets in evidence references or private reasons.
- Run `python manage.py prune_public_storefront_metrics` on a scheduled maintenance cadence.
  The configured retention is 24 months.
- Run `python manage.py backfill_public_receipt_identities` once after deployment and after
  any controlled import of historical internal receipts.
- After release, open a profile URL and its SVG QR target using the public origin, then verify
  one sale and one return receipt token. Confirm all public HTML is served with `no-store`.
- A suspected leak requires immediate platform suspension, evidence preservation, incident
  assessment, correction, cache/search removal requests where applicable, and documented
  reinstatement approval.
- Backups and restore drills must include profiles, contact/opening-hour rows, immutable
  events, verification requests/decisions, aggregate metrics, and sale/return receipt
  identity sidecars.

## Private document operations

- Production must use private durable object storage and a non-development scanner.
  `python manage.py check --deploy` flags local storage and rejects the development scanner.
- Run `python manage.py reconcile_document_storage` on a scheduled cadence. It reports
  missing objects, orphan objects, hash mismatches, stale quarantine, and incomplete target
  links. Investigate before using `--delete-orphans`; the command retains objects newer
  than `DOCUMENT_ORPHAN_RETENTION_HOURS` so an in-flight upload cannot be deleted.
- Run `python manage.py purge_document_files --dry-run` before each scheduled retention purge.
  The preview reports business/document IDs and file counts without exposing storage keys or
  filenames. Then run `python manage.py purge_document_files` for eligible cancelled
  unconfirmed sources and retain its per-document output as the audit record. Metadata,
  hashes, revisions, and purge timestamps remain.
- Scanner timeout or error leaves the document quarantined. Restore scanner health, retry
  from the document page, and never mark bytes clean manually.
- An infected source is rejected and its bytes are removed; preserve only the bounded scan
  result required by the approved incident policy.
- Do not enable real-document use until private-storage, scanner, retention/legal/privacy,
  backup/restore, tenant-isolation, dependency, upload-abuse, and native-language reviews
  are approved.
