# Data model

## Foundation relationships

```mermaid
erDiagram
    USER ||--o{ BUSINESS_MEMBERSHIP : has
    BUSINESS ||--o{ BUSINESS_MEMBERSHIP : grants
    BUSINESS ||--o{ BRANCH : operates
    BRANCH o|--o{ BUSINESS_MEMBERSHIP : assigns
    BUSINESS ||--o{ ATTENDANCE_RECORD : owns
    BRANCH ||--o{ ATTENDANCE_RECORD : records
    BUSINESS_MEMBERSHIP ||--o{ ATTENDANCE_RECORD : attends
    ATTENDANCE_RECORD ||--o{ ATTENDANCE_CORRECTION : has
    BUSINESS_MEMBERSHIP ||--o{ ATTENDANCE_CORRECTION : makes
    BUSINESS ||--o{ CATEGORY : owns
    BUSINESS ||--o{ PRODUCT : owns
    BUSINESS ||--o{ PRODUCT_VARIANT : owns
    CATEGORY o|--o{ PRODUCT : groups
    PRODUCT ||--o{ PRODUCT_VARIANT : offers

    USER {
        bigint id PK
        string email UK
        string full_name
        boolean is_active
    }
    BUSINESS {
        uuid id PK
        string name
        string slug UK
        string business_type
        boolean is_active
    }
    BRANCH {
        uuid id PK
        uuid business_id FK
        string name
        string code
        boolean is_active
    }
    BUSINESS_MEMBERSHIP {
        uuid id PK
        uuid business_id FK
        bigint user_id FK
        uuid assigned_branch_id FK
        string role
        boolean is_active
    }
    ATTENDANCE_RECORD {
        uuid id PK
        uuid business_id FK
        uuid branch_id FK
        uuid employee_id FK
        date work_date
        string status
        datetime check_in_at
        datetime check_out_at
    }
    ATTENDANCE_CORRECTION {
        uuid id PK
        uuid business_id FK
        uuid attendance_id FK
        uuid corrected_by_id FK
        string reason
        datetime created_at
    }
    CATEGORY {
        uuid id PK
        uuid business_id FK
        string name
        string slug
        boolean is_active
    }
    PRODUCT {
        uuid id PK
        uuid business_id FK
        uuid category_id FK
        string name
        boolean is_active
        boolean public_visibility
    }
    PRODUCT_VARIANT {
        uuid id PK
        uuid business_id FK
        uuid product_id FK
        string sku
        string size
        string color
        decimal selling_price
        decimal cost_price
        boolean is_active
    }
```

## Constraints

- one membership per user and business;
- one attendance record per business, employee, and work date;
- one branch code per business;
- one category slug per business;
- one product name per business;
- one variant SKU per business;
- non-negative selling and optional cost prices;
- model validation prevents cross-business category/product relationships.
- model validation prevents cross-business attendance, branch, employee, and correction
  relationships;
- attendance check-out cannot precede check-in;
- attendance corrections preserve before-and-after values and cannot be edited normally.

## Planned ledger entities

Future slices should add:

- inventory movement and balance projection;
- sale and immutable sale line snapshots;
- payment and payment allocation;
- receipt;
- cash session and cash movement;
- return, refund, void, and reversal;
- stock count and reconciliation;
- audit event.

Operational records must carry both `business_id` and `branch_id`, immutable posted
timestamps, actor identity, status, and idempotency identifiers where requests can be
retried.
