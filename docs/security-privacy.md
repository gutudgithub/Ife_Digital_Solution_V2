# Security and privacy

## Current controls

- email-based custom user identity;
- Django password hashing and password validators;
- server-side authorization from active business membership;
- tenant-filtered dashboard and catalog queries;
- CSRF protection and POST-only logout;
- HTTP-only session and CSRF cookies;
- frame denial and content-type sniffing protection;
- secure cookies, HTTPS redirect, and HSTS when debug mode is disabled;
- database constraints plus model validation for tenant-linked catalog records;
- secrets loaded from environment variables and excluded from version control;
- non-root application process in the container;
- automated tenant-isolation and role-permission tests.
- owner/manager-only performance endpoints and exports, with no assigned-cost or result
  disclosure to cashiers, stock employees, inactive members, or unrelated platform staff;
- report branch choices derived only from the active tenant and validated again in the
  reporting service.

Performance CSV files contain commercially sensitive sales, cost, margin, expense, and
inventory-value evidence. Operators must store and share exports under the same access and
retention controls as the application; the product does not create public export links.

## Required before real customer data

- deploy behind trusted HTTPS termination with explicit hosts and CSRF origins;
- use a managed secret store and rotate credentials;
- enforce least-privilege database and infrastructure roles;
- add rate limiting and monitoring for authentication abuse;
- define session duration, password reset, account recovery, and optional MFA policy;
- add complete audit coverage for privileged and financial actions;
- test backup restoration and incident response;
- document data inventory, legal basis, retention, deletion, export, breach response, and
  processor responsibilities;
- perform threat modeling, dependency scanning, security review, and penetration testing;
- verify tenant isolation for every new business-owned model and endpoint.

## Privacy boundary

The data model should minimize personal data and avoid collecting fields without a defined
product need and retention period. Production processing must be reviewed against Ethiopia
Personal Data Protection Proclamation No. 1321/2024 and any applicable directives and
sector requirements.

## Tax and receipt boundary

Pilot receipts are internal transaction evidence only. The application must not claim
official invoice, fiscal receipt, VAT filing, or electronic-invoicing compliance until
qualified Ethiopian legal and tax review confirms the requirements and acceptance evidence.

Relevant primary sources:

- [Personal Data Protection Proclamation No. 1321/2024](https://justice.gov.et/en/?jet_download=a98f4044104aa163dfb32188423c0d3c3d53e3da)
- [Electronic Transaction Proclamation No. 1205/2020](https://justice.gov.et/am/law/electronic-transaction-proclamation/)
- [Electronic Invoicing System Administration Directive No. 1142/2026](https://justice.gov.et/en/directives/electronic-invoicing-system-administration-directive/)
- [Value Added Tax Regulation No. 570/2025](https://justice.gov.et/en/law/%e1%8b%a8%e1%89%b0%e1%8c%a8%e1%88%9b%e1%88%aa-%e1%8a%a5%e1%88%b4%e1%8d%a5-%e1%8a%a5%e1%88%b4%e1%89%b5-%e1%89%b3%e1%8a%ad%e1%88%b5-%e1%8b%b0%e1%8a%95%e1%89%a5-%e1%89%81%e1%8c%a5%e1%88%ad-570-2017-regulation-no-570-2025/)
- [OWASP Application Security Verification Standard](https://asvs.dev/)

These references do not constitute legal, tax, accounting, privacy, or security
certification.
