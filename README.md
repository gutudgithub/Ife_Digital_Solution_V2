# Ife Digital Solution

Ife Digital Solution is a business-control platform for Ethiopian small businesses. The
foundation is reusable for inventory-based retail while the first pilot is tailored to
clothing and footwear.

This repository contains the independently reviewed Stages 1 through 5 and 7, plus the
implemented Stage 8 private-document slice:

- email-based authentication and Django administration;
- business and branch records;
- owner, manager, and cashier memberships;
- server-side active-business scoping;
- categories, products, and size/color product variants;
- business-scoped product names and SKUs;
- authenticated dashboard and product creation;
- branch-scoped employee check-in and check-out;
- owner/manager attendance review and auditable corrections;
- business-scoped suppliers, purchases, partial receiving, and immutable goods receipts;
- branch stock balances derived from immutable inventory movements;
- perpetual moving weighted-average cost, opening balances, and controlled adjustments;
- purchase returns traced to exact receipt lines with full compensating reversals;
- supplier activity, purchase-cost history, and filtered movement history;
- fully paid cash or manually referenced Telebirr sales;
- immutable sale inventory movements, payment evidence, and internal receipts;
- exact-line customer returns and full sale reversals with immutable refund evidence;
- original-assigned-cost stock restoration and controlled return reversals;
- branch cash sessions, immutable drawer movements, physical counts, and variance evidence;
- paid operating expenses and purchase-linked supplier settlement evidence;
- complete blind branch stock counts, inventory-posting freezes, approval, and exact-cost
  reversal evidence;
- owner/manager performance intelligence with reconciled net sales, assigned inventory cost,
  operational result, margins, growth, SKU and expense analysis, accessible graphs, CSV, and
  print output;
- owner-controlled public business profiles, selected catalog browsing, optional public
  prices, SVG QR codes, specific staff-reviewed indicators, aggregate opens, and minimal
  internal-receipt verification;
- private JPEG, PNG, and PDF capture, fail-closed scanning, human transcription, owner
  confirmation, and source-to-operational-record traceability for purchases, expenses, and
  opening stock;
- English, Amharic, and Afaan Oromoo locale configuration;
- PostgreSQL-compatible settings, Docker Compose, CI, and pre-commit checks.

Customer and loyalty work remains deferred. Offline, multi-branch, SaaS, and
production-launch stages remain separately gated delivery slices.

## Requirements

- Python 3.12
- PostgreSQL 17 for the container workflow
- Docker with Compose, if using containers

## Evaluate the demo with Docker

After extracting the source ZIP, open a terminal in the project folder and run:

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec web python manage.py seed_demo --password "Choose-A-Local-Password"
```

On Windows PowerShell, replace the first command with:

```powershell
Copy-Item .env.example .env
```

Open `http://localhost:8000/login/` and sign in with the password you chose:

- owner: `owner@demo.ife.local`;
- cashier: `cashier@demo.ife.local`;
- stock employee: `stock@demo.ife.local`.

The owner can manage products, suppliers, purchases, receiving, returns, expenses, supplier
settlement, cash sessions, stock counts, sales, history, attendance, and performance reports.
The seeded purchase
`DEMO-PUR-001` includes a posted receipt and one posted supplier return, so inventory,
supplier activity, cost history, and return screens have evidence immediately. The demo also
posts cash and Telebirr sales across multiple dates, customer returns and a return reversal,
two expense categories and an expense reversal, supplier-settlement evidence, a closed cash
session with variance, and approved/reversed stock-count evidence.
It also publishes a demo storefront with two products, showing prices for one and hiding
prices for the other. The owner manages it at `/public-profile/`; `seed_demo` prints its
stable public URL.
The document inbox contains a clean demo purchase source and transcription awaiting owner
confirmation. Confirming it creates a normal purchase draft without receiving stock.
The stock employee can receive approved purchases, prepare return drafts, enter blind stock
counts, and submit complete counts for the assigned branch, but cannot approve or reverse
counts, post/reverse returns, adjust stock, or view inventory values.
The cashier can record assigned-branch sales, prepare customer-return drafts, and record
personal attendance but cannot post refunds, create full sale reversals, see suppliers,
purchase returns, purchase costs, movement history, average costs, or inventory values.
When finished, stop the containers with `docker compose down`.

## Local setup with SQLite

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pre-commit install
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

SQLite is used when `POSTGRES_HOST` is unset. Sign in to `/admin/`, then create records in
this order:

1. a business;
2. its branch;
3. a membership connecting the superuser to the business as owner;
4. categories and product variants as needed.

The dashboard is at `/`, the product catalog at `/catalog/`, inventory and movement history
at `/inventory/`, purchasing at `/purchasing/`, purchase returns at
`/purchasing/returns/`, sales at `/sales/`, cash sessions at `/cash/`, expenses at
`/expenses/`, stock counts at `/inventory/counts/`, and attendance at `/attendance/`. Sale
corrections are listed at `/sales/returns/`. Owner/manager performance intelligence is at
`/performance/`. Public-profile management is at `/public-profile/`. Private document
capture and review is at `/documents/`.

## Local setup with PostgreSQL and Docker

```bash
cp .env.example .env
docker compose up --build
```

In another terminal:

```bash
docker compose exec web python manage.py createsuperuser
```

The `.env.example` values are development-only. Replace every production secret and do not
commit `.env`.

## Quality checks

```bash
ruff check .
ruff format --check .
mypy apps config
python manage.py makemigrations --check --dry-run
python manage.py check
pytest
```

Production configuration should also pass:

```bash
DJANGO_DEBUG=false \
DJANGO_SECRET_KEY=deployment-check-7Yf2K9qP4mX8vN6cR3sW5tZ1bH0jL7dG9aE2 \
DJANGO_ALLOWED_HOSTS=example.com \
python manage.py check --deploy
```

## Documentation

- [Product scope](docs/product-scope.md)
- [Architecture](docs/architecture.md)
- [Business rules](docs/business-rules.md)
- [Data model](docs/data-model.md)
- [Security and privacy](docs/security-privacy.md)
- [Operations](docs/operations.md)
- [Owner-to-cashier operating guide](docs/operating-guide.md)
- [Roadmap](docs/roadmap.md)
- [Claude review guide](docs/claude-review-guide.md)
- [Architecture decisions](docs/decisions/)

## Status and boundaries

The application is an early foundation, not a production accounting or tax system. Receipts
planned for the pilot are internal transaction receipts only. Official tax or electronic
invoicing must remain disabled until Ethiopian legal, tax, and Ministry of Revenues
requirements have been independently verified and implemented.
