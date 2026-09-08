# Data model

## Foundation relationships

```mermaid
erDiagram
    USER ||--o{ BUSINESS_MEMBERSHIP : has
    BUSINESS ||--o{ BUSINESS_MEMBERSHIP : grants
    BUSINESS ||--o{ BRANCH : operates
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
        string role
        boolean is_active
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
- one branch code per business;
- one category slug per business;
- one product name per business;
- one variant SKU per business;
- non-negative selling and optional cost prices;
- model validation prevents cross-business category/product relationships.

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
