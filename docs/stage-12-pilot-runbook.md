# Stage 12 controlled-pilot runbook

## Scope

The pilot is limited to one approved business, one active operating branch, named staff,
controlled devices, and browser delivery. It does not authorize public checkout, provider
payment verification, multi-branch transfers, customer or loyalty records, statutory
accounting, official tax invoices, or unrestricted production use.

## Go/no-go record

Deployment operators must retain dated evidence for every item below. An unchecked item is a
no-go, not an implied approval.

- [ ] Product owner approved the exact pilot business, branch, dates, and named operators.
- [ ] Accountant approved or explicitly qualified Stage 5 formulas and omissions.
- [ ] Native speakers approved the Amharic and Afaan Oromoo catalogs.
- [ ] Security and privacy/legal reviewers approved the deployment and data handling.
- [ ] HTTPS, explicit hosts, secure cookies, CSP, monitoring, and shared rate limits are live.
- [ ] Product/payment media and private documents use private durable object storage.
- [ ] Production malware scanning and the document-retention policy are approved.
- [ ] PostgreSQL and both object stores were restored in isolation within approved RPO/RTO.
- [ ] Incident contacts, rollback authority, support hours, and communication paths are named.
- [ ] Owner, manager, cashier, and stock operators completed scenario-based training.
- [ ] Android Chrome, iPhone Safari, Chrome, Firefox, Edge, keyboard, zoom, and print checks pass.
- [ ] Independent Claude review findings are resolved or explicitly accepted.

Run `python manage.py check --deploy` and
`python manage.py check_stage12_readiness` against the release configuration. Retain the
output with the release identifier. These commands validate recorded configuration; they do
not perform or approve the human work.

## Release preparation

1. Freeze the reviewed revision and record its commit identifier and image digest.
2. Export and verify the current schema migration plan.
3. Take an encrypted PostgreSQL backup and protected object-storage recovery point.
4. Confirm catalog and Telebirr media reconciliation is clean:
   `python manage.py reconcile_catalog_media`.
5. Confirm document reconciliation is clean:
   `python manage.py reconcile_document_storage`.
6. Verify the staff-entry QR resolves only to the production HTTPS sign-in page.
7. Test-scan each active branch Telebirr QR outside Ife and compare the merchant identity.
8. Verify owner, manager, cashier, and stock accounts on separate controlled browser profiles.
9. Print the manual transaction sheet, provisional offline-note procedure, support contacts,
   incident contacts, and rollback checklist.

## Operator golden path

1. Owner signs in from the staff-entry QR and verifies the expected HTTPS host.
2. Manager uploads one product image with useful alternative text and checks internal and
   public card views.
3. Cashier creates a sale through the visual picker, then repeats through the ordinary
   server-rendered line fields with JavaScript disabled.
4. For cash, use the open cash session and reconcile the physical drawer normally.
5. For Telebirr:
   - compare the displayed merchant name, identifier, and exact ETB amount;
   - let the customer use their Telebirr application;
   - do not treat QR display or scanning as payment evidence;
   - observe the successful customer result;
   - enter the normalized transaction reference; and
   - post only through the ordinary atomic sale-posting action.
6. Verify the internal receipt says the Telebirr reference was manually observed.
7. Publish only explicitly selected products. Confirm anonymous pages expose no SKU, cost,
   stock balance, exact availability, supplier, staff, branch-private data, or payment
   reference.
8. Complete returns, cash close, purchasing, receiving, expenses, supplier settlement,
   stock count, document transcription, performance review, and daily reconciliation using
   their existing controlled workflows.

## Daily controls

- Reconcile posted sales, payment methods, Telebirr references, receipts, cash movements,
  returns, refunds, expenses, stock movements, and unresolved offline drafts.
- Review security, application, storage, scanner, backup, and capacity alerts.
- Review failed login, rate-limit, forbidden-access, upload, and synchronization events
  without copying secrets or payment references into support notes.
- Run catalog-media and document-storage reconciliation on the approved cadence.
- Confirm no operator shared credentials or left an authenticated shared-device session open.
- Record incidents, workarounds, customer impact, and the named decision owner.

## Offline and manual fallback

- Offline mode creates local sale drafts only. It never posts inventory, payment, cash, or a
  receipt.
- Use the approved provisional-note procedure during a network outage. Preserve original
  sale time and drafting identity, then synchronize and review conflicts after reconnection.
- If the browser queue is unavailable, use the numbered manual transaction sheet. Do not
  invent a receipt number or mark payment provider verification.
- If Telebirr QR display is unavailable, the cashier may use the separately controlled
  merchant display only if the owner-approved manual procedure identifies the same branch
  merchant. The transaction reference remains mandatory.
- Stop payment posting if merchant identity, amount, reference, branch, or operator authority
  cannot be established.

## Incident response

1. Classify severity and name the incident commander.
2. Protect people and evidence; do not delete audit records or affected objects.
3. Contain access with the narrowest safe action: suspend a public profile, deactivate a QR,
   disable an account, revoke a secret, or stop the affected release.
4. Assess tenant scope, payment evidence, stored objects, credentials, and legal/privacy
   notification obligations.
5. Communicate through the approved contact path without exposing source documents, payment
   references, secrets, or unnecessary personal data.
6. Restore service only after the named security/privacy and product owners approve.
7. Record timeline, root cause, corrective actions, and follow-up verification.

## Rollback

1. Stop new traffic or place the pilot in controlled maintenance mode.
2. Preserve logs, database state, object versions, and the release identifier.
3. Roll application instances back to the last reviewed compatible image.
4. Never reverse a migration blindly. Use the reviewed migration-specific rollback or restore
   procedure.
5. If integrity is uncertain, restore PostgreSQL and both object stores to one consistent
   recovery point in an isolated environment first.
6. Reconcile tenant counts, inventory movements and balances, receipts, cash, returns,
   offline sync evidence, documents and hashes, product images, and Telebirr events.
7. Resume only after the product owner and deployment operator sign the go/no-go record.

## Account recovery

The controlled pilot has no self-service password-reset workflow. A named administrator must
verify the staff member through the approved out-of-band procedure, reset access through the
standard Django administration boundary, require a new unique password, revoke exposed
sessions when necessary, and record the administrative action. Never send passwords, session
tokens, or reset links through an unapproved channel.

## Recovery evidence

Run isolated restore drills for PostgreSQL, private documents, and catalog/payment media.
Record approver, evidence URL, database and object restore timestamps, measured RPO, and
measured RTO with:

```text
python manage.py record_recovery_attestation \
  --approved-by "Named operator" \
  --evidence-url "https://approved-evidence.example/restore-report" \
  --database-restore-tested-at "2026-09-20T08:00:00Z" \
  --object-restore-tested-at "2026-09-20T08:15:00Z" \
  --measured-rpo-hours "1" \
  --measured-rto-hours "4" \
  --output "/run/secrets/ife-recovery-attestation.json"
```

The recommended maximums are one hour RPO and four hours RTO. A successful backup job alone
is not restore evidence.
