# Claude review guide

Claude acts as an independent reviewer and should not directly rewrite the implementation
branch during review.

## Review inputs

Provide:

1. the approved feature brief and acceptance criteria;
2. relevant architecture decisions and business rules;
3. the pull-request diff;
4. migration output;
5. test, lint, type-check, and deployment-check evidence;
6. unresolved product, accounting, tax, privacy, translation, or operational decisions.

## Review order

1. requirement coverage and scope boundaries;
2. accounting, inventory, payment, and cash invariants;
3. tenant isolation and role authorization;
4. data-model integrity and migration safety;
5. transaction atomicity, idempotency, and concurrency;
6. security, privacy, audit, and secret handling;
7. failure recovery, backup, monitoring, and support impact;
8. localization and usability;
9. tests and regression risk;
10. maintainability and unnecessary complexity.

## Finding format

Each finding should contain:

- severity: critical, high, medium, or low;
- violated requirement or invariant;
- affected file, symbol, or workflow;
- evidence and reproduction or reasoning;
- recommended correction;
- required regression test.

Critical and high findings block acceptance. Low-severity nits that require disproportionate
scope should be presented as optional follow-up decisions.

## Decision boundary

Claude may identify ambiguity and recommend options, but the product owner decides product,
accounting, tax, legal, privacy, translation, priority, and acceptance questions. Devin
implements approved corrections and supplies new evidence for re-review.
