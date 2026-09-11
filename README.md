# Ife Digital Solution

Ife Digital Solution is a business-control platform for Ethiopian small businesses. The
foundation is reusable for inventory-based retail while the first pilot is tailored to
clothing and footwear.

This repository contains the independently reviewed Stage 1 foundation, Stage 2A inventory
ledger, and Stage 2B purchase-return lifecycle:

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
- English, Amharic, and Afaan Oromoo locale configuration;
- PostgreSQL-compatible settings, Docker Compose, CI, and pre-commit checks.

Sales, payments, internal receipts, cash close, reconciliation, and later reports remain
separate reviewed delivery slices.

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

The owner can manage products, suppliers, purchases, receiving, purchase returns and
reversals, opening stock, adjustments, history, and attendance. The seeded purchase
`DEMO-PUR-001` includes a posted receipt and one posted supplier return, so inventory,
supplier activity, cost history, and return screens have evidence immediately. The stock
employee can receive approved purchases and prepare return drafts for the assigned branch,
but cannot approve purchases, post/reverse returns, adjust stock, or view inventory values.
The cashier can view branch stock quantities and record personal attendance but cannot see
suppliers, returns, purchase costs, movement history, average costs, or inventory values.
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
`/purchasing/returns/`, and attendance at `/attendance/`.

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
- [Roadmap](docs/roadmap.md)
- [Claude review guide](docs/claude-review-guide.md)
- [Architecture decisions](docs/decisions/)

## Status and boundaries

The application is an early foundation, not a production accounting or tax system. Receipts
planned for the pilot are internal transaction receipts only. Official tax or electronic
invoicing must remain disabled until Ethiopian legal, tax, and Ministry of Revenues
requirements have been independently verified and implemented.
