# ADR 0001: Modular Django monolith

- Status: accepted
- Date: 2026-09-08

## Context

The pilot needs reliable business rules, server-side permissions, auditable PostgreSQL
transactions, bilingual templates, and fast iteration by a small product team. The original
proposal combined server-rendered Django with a separate frontend directory, creating an
unnecessary architectural conflict.

## Decision

Use one modular Django application with PostgreSQL and server-rendered templates. Use HTMX
only for focused interaction and add API endpoints only when a concrete integration or
client requires them.

Modules align to business boundaries. All tenant-owned records include direct business
scope. Financial capabilities will be added as ledger-oriented vertical slices.

## Consequences

- one deployment and transaction boundary reduces pilot complexity;
- Django authentication, administration, forms, localization, and CSRF protection are
  available without duplicate application layers;
- modules must preserve clear ownership and avoid a single undifferentiated models package;
- an independent frontend can still be introduced later through an approved decision if
  product evidence justifies the cost.
