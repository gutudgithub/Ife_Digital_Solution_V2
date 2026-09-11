# Stage 1 feature brief: foundation hardening and basic attendance

## Status

Approved for implementation on 8 September 2026 under product decision D16.

## Outcome

An active employee can record one branch-scoped attendance record per Addis Ababa business
date. Owners and managers can review attendance and make reasoned corrections without losing
the previous values. The feature establishes reusable authorization and audit patterns without
introducing payroll or employment-compliance calculations.

## Included

- optional primary-branch assignment on a business membership;
- self-service check-in and check-out for active members;
- check-out of an open attendance record for up to 18 hours, including across midnight;
- one attendance record per member and business date;
- present, absent, and excused statuses;
- owner/manager attendance overview with date filtering and pagination;
- employee view restricted to the signed-in member's records;
- owner/manager corrections with a mandatory reason;
- append-only before-and-after correction history through normal application/admin flows;
- translation-ready server-rendered interfaces;
- branch, tenant, permission, concurrency, and validation tests.

Attendance is online-only in this stage.

## Excluded

- payroll, wages, overtime valuation, deductions, tax, benefits, and payslips;
- leave balances, leave approval, scheduling, shifts, biometrics, and geolocation;
- offline attendance and automatic absence generation;
- statutory employment reporting.

## Roles and authorization

- Any active business member may view their own attendance and check in or out.
- Owners and managers may view all attendance in the active business.
- Owners and managers may create or correct attendance for active members of the active
  business.
- Owners and managers may correct existing attendance for deactivated members of the active
  business, while creating new attendance still requires an active member.
- Cashiers may not view or correct another member's attendance.
- A member assigned to a branch records self-service attendance only in that branch.
- An unassigned member may use self-service attendance only when the business has exactly one
  active branch; otherwise an owner or manager must assign a branch.
- Every submitted business, branch, membership, and attendance identifier is resolved inside
  the active tenant.

## Data model

### Business membership

Add an optional `assigned_branch` relationship. Validation requires the branch and membership
to belong to the same business.

### Attendance record

Each record carries:

- globally unique identifier;
- direct business and branch scope;
- employee business membership;
- Addis Ababa business date;
- status;
- optional check-in and check-out timestamps;
- creation and update timestamps.

The database enforces one record per business, employee, and business date. Normal instance
writes run model validation for matching business scope and require check-out to be at or
after check-in. Django bulk writes and raw SQL bypass that application-level validation;
database-level cross-table triggers remain deferred until the production database-role and
migration policy is designed.

### Attendance correction

Each correction carries:

- direct business scope and a link to the attendance record;
- correcting owner/manager membership;
- required reason;
- previous and replacement status, check-in, and check-out values;
- creation timestamp.

Corrections are append-only through normal instance, application, and admin flows. Django
queryset updates/deletes and raw SQL can bypass the instance guard until production database
privileges or triggers are introduced. The current attendance record and its correction
snapshot are written in one transaction.

## Workflows

### Self check-in

1. Resolve the active membership and permitted branch on the server.
2. Lock or create today's attendance row inside a transaction.
3. Reject a repeated check-in.
4. Store the current server timestamp and present status.

### Self check-out

1. Resolve and lock today's attendance row inside a transaction.
2. Require a prior check-in and no existing check-out.
3. Store the current server timestamp.

### Manager correction

1. Resolve the attendance record inside the active business.
2. Validate replacement values and a non-blank reason.
3. Lock the record.
4. Append a before-and-after correction snapshot.
5. Update the current record atomically.

## Interface

- Add an Attendance item to authenticated primary navigation.
- Show today's status with check-in or check-out action when available.
- When an earlier business date remains open, name that date before offering check-out.
- Show an accessible attendance table.
- Owners/managers see employee, branch, status, times, and correction action.
- Other roles see only their own entries.
- Correction history appears with actor, time, reason, and before/after values.

## Acceptance criteria

1. A demo owner and cashier assigned to the pilot branch can check in and out.
2. A repeated check-in or check-out does not create a duplicate or overwrite history.
3. A cashier cannot view or modify another employee's attendance.
4. An owner or manager can correct attendance only with a reason.
5. Every correction preserves before-and-after values and the correcting actor.
6. Cross-business branch, membership, record, and correction access is rejected.
7. One member cannot have duplicate attendance rows for the same business date.
8. A check-out earlier than check-in is rejected.
9. All user-facing strings are translation-ready.
10. Ruff, formatting, mypy, migration checks, Django checks, and affected tests pass.

## Independent review focus

Claude should review tenant isolation, branch assignment, permission boundaries, correction
immutability, timezone behavior, concurrent check-in safety, and the absence of payroll logic.
