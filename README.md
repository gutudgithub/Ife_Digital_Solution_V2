# Ife Digital Solution

Ife Digital Solution is a business-control platform for Ethiopian small businesses. The
foundation is reusable for inventory-based retail while the first pilot is tailored to
clothing and footwear.

This repository currently contains the Phase 1 foundation:

- email-based authentication and Django administration;
- business and branch records;
- owner, manager, and cashier memberships;
- server-side active-business scoping;
- categories, products, and size/color product variants;
- business-scoped product names and SKUs;
- authenticated dashboard and product creation;
- English, Amharic, and Afaan Oromoo locale configuration;
- PostgreSQL-compatible settings, Docker Compose, CI, and pre-commit checks.

Inventory movements, sales, payments, internal receipts, cash close, reconciliation, and
reports are intentionally separate reviewed delivery slices.

## Requirements

- Python 3.12
- PostgreSQL 17 for the container workflow
- Docker with Compose, if using containers

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

The dashboard is at `/` and the product catalog is at `/catalog/`.

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
