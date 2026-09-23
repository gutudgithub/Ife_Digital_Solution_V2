# Repository instructions

## Product boundaries

- Keep the domain reusable for inventory-based retail.
- Tailor examples and acceptance scenarios to clothing and footwear.
- Treat pilot receipts as internal transaction receipts, never official tax invoices.
- Keep statutory accounting, VAT filing, payroll, and electronic invoicing out of scope unless
  an approved feature brief explicitly adds them.

## Architecture

- Use a modular Django monolith with server-rendered templates.
- Every business-owned record must have explicit business scope.
- Never trust a business identifier from a form or URL without validating the current user's
  active membership.
- Keep branch identifiers in operational models even while the pilot has one active branch.
- Use `DecimalField` and `Decimal` for money; never use floating point.
- Store timestamps in UTC and display dates in `Africa/Addis_Ababa`.

## Transaction invariants

- Draft records have no ledger effect.
- Posted records are immutable through normal flows.
- Corrections use explicit reversal, void, return, refund, or authorized adjustment events.
- Inventory is derived from movement history.
- Negative stock is disallowed by default.
- Posting must be atomic, idempotent, auditable, and safe under concurrency.
- Application code must write business-scoped operational records through their service layer;
  do not bypass validation and audit flows with bulk ORM creates, updates, or deletes.
- Never modify tests solely to make an implementation pass.

## Development

- Use Python 3.12 and dependency versions declared in `pyproject.toml`.
- Create migrations with Django; do not hand-edit generated migration files.
- Add type annotations to application code and keep `mypy apps config` passing.
- Run Ruff, migration checks, Django checks, and the affected tests before committing.
- Do not commit `.env`, credentials, customer data, generated static files, or local databases.
- User-facing translations require native-speaker review before acceptance.

## Review workflow

Devin implements and integrates. Claude reviews the approved feature brief, architecture
decisions, pull-request diff, and test evidence independently. Product, accounting, tax,
legal, privacy, translation, and final-acceptance decisions remain human decisions.
