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
   movement/balance reconciliation. Never use `seed_demo` in production.

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
