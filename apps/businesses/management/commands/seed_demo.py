import uuid
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import (
    Branch,
    Business,
    BusinessMembership,
    BusinessType,
    MembershipRole,
)
from apps.cash.models import CashMovementType
from apps.cash.services import (
    close_cash_session,
    open_cash_session,
    post_manual_cash_movement,
)
from apps.catalog.models import Category, Product, ProductVariant, StockUnit
from apps.expenses.models import (
    ExpenseCategory,
    ExpenseSettlementPostingKey,
    OperatingExpense,
    OperationalPaymentMethod,
    SupplierPayment,
    SupplierReturnSettlement,
    SupplierReturnSettlementType,
)
from apps.expenses.services import (
    create_operating_expense_draft,
    post_operating_expense,
    post_supplier_payment,
    post_supplier_return_settlement,
)
from apps.purchasing.models import (
    Purchase,
    PurchaseLine,
    Supplier,
)
from apps.purchasing.services import (
    ReceiptQuantity,
    approve_purchase,
    post_purchase_return,
    receive_purchase,
    save_purchase_return_draft,
)
from apps.purchasing.services import (
    ReturnQuantity as PurchaseReturnQuantity,
)
from apps.sales.models import Sale, SalePostingKey, SaleReturnPostingKey, SaleReturnPurpose
from apps.sales.services import (
    ReturnQuantity as SaleReturnQuantity,
)
from apps.sales.services import (
    SaleQuantity,
    post_sale,
    post_sale_return,
    save_sale_draft,
    save_sale_return_draft,
)


class Command(BaseCommand):
    help = "Create idempotent local demo data for evaluating the foundation workflows."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--password",
            required=True,
            help="Password assigned to both local demo users.",
        )

    def handle(self, *args: object, **options: object) -> None:
        if not settings.DEBUG:
            raise CommandError("Demo data can only be created when DJANGO_DEBUG is enabled.")

        password = options["password"]
        if not isinstance(password, str) or len(password) < 8:
            raise CommandError("The demo password must contain at least 8 characters.")

        with transaction.atomic():
            owner = self._upsert_user(
                email="owner@demo.ife.local",
                full_name="Demo Owner",
                password=password,
            )
            cashier = self._upsert_user(
                email="cashier@demo.ife.local",
                full_name="Demo Cashier",
                password=password,
            )
            stock_employee = self._upsert_user(
                email="stock@demo.ife.local",
                full_name="Demo Stock Employee",
                password=password,
            )
            business, _ = Business.objects.update_or_create(
                slug="ife-demo-fashion",
                defaults={
                    "name": "Ife Demo Fashion",
                    "business_type": BusinessType.CLOTHING_FOOTWEAR,
                    "is_active": True,
                },
            )
            branch, _ = Branch.objects.update_or_create(
                business=business,
                code="main",
                defaults={"name": "Main Store", "is_active": True},
            )
            owner_membership, _ = BusinessMembership.objects.update_or_create(
                business=business,
                user=owner,
                defaults={
                    "assigned_branch": branch,
                    "role": MembershipRole.OWNER,
                    "is_active": True,
                },
            )
            cashier_membership, _ = BusinessMembership.objects.update_or_create(
                business=business,
                user=cashier,
                defaults={
                    "assigned_branch": branch,
                    "role": MembershipRole.CASHIER,
                    "is_active": True,
                },
            )
            BusinessMembership.objects.update_or_create(
                business=business,
                user=stock_employee,
                defaults={
                    "assigned_branch": branch,
                    "role": MembershipRole.STOCK_EMPLOYEE,
                    "is_active": True,
                },
            )
            supplier, _ = Supplier.objects.update_or_create(
                business=business,
                name="Demo Addis Wholesale",
                defaults={
                    "phone": "+251 900 000 000",
                    "notes": "Local demonstration supplier.",
                    "is_active": True,
                },
            )
            clothing, _ = Category.objects.update_or_create(
                business=business,
                slug="clothing",
                defaults={"name": "Clothing", "is_active": True},
            )
            footwear, _ = Category.objects.update_or_create(
                business=business,
                slug="footwear",
                defaults={"name": "Footwear", "is_active": True},
            )
            shirt, _ = Product.objects.update_or_create(
                business=business,
                name="Classic T-Shirt",
                defaults={
                    "category": clothing,
                    "description": "Everyday cotton shirt.",
                    "is_active": True,
                },
            )
            shoes, _ = Product.objects.update_or_create(
                business=business,
                name="Leather Shoes",
                defaults={
                    "category": footwear,
                    "description": "Smart leather footwear.",
                    "is_active": True,
                },
            )
            variants = (
                (
                    shirt,
                    "TSHIRT-BLK-M",
                    "M",
                    "Black",
                    Decimal("850.00"),
                    Decimal("520.00"),
                    StockUnit.PIECE,
                    Decimal("5"),
                ),
                (
                    shirt,
                    "TSHIRT-WHT-L",
                    "L",
                    "White",
                    Decimal("850.00"),
                    Decimal("520.00"),
                    StockUnit.PIECE,
                    Decimal("5"),
                ),
                (
                    shoes,
                    "SHOE-BRN-42",
                    "42",
                    "Brown",
                    Decimal("3200.00"),
                    Decimal("2100.00"),
                    StockUnit.PAIR,
                    Decimal("3"),
                ),
                (
                    shoes,
                    "SHOE-BLK-43",
                    "43",
                    "Black",
                    Decimal("3200.00"),
                    Decimal("2100.00"),
                    StockUnit.PAIR,
                    Decimal("3"),
                ),
            )
            demo_variants: dict[str, ProductVariant] = {}
            for (
                product,
                sku,
                size,
                color,
                selling_price,
                cost_price,
                stock_unit,
                low_stock_threshold,
            ) in variants:
                variant, _ = ProductVariant.objects.update_or_create(
                    business=business,
                    sku=sku,
                    defaults={
                        "product": product,
                        "size": size,
                        "color": color,
                        "selling_price": selling_price,
                        "cost_price": cost_price,
                        "stock_unit": stock_unit,
                        "low_stock_threshold": low_stock_threshold,
                        "is_active": True,
                    },
                )
                demo_variants[sku] = variant

            cash_session = open_cash_session(
                actor=owner_membership,
                branch=branch,
                opening_float=Decimal("500.00"),
                idempotency_key=uuid.UUID("00000000-0000-4000-8000-000000000205"),
            )
            purchase = Purchase.objects.filter(
                business=business,
                internal_number="DEMO-PUR-001",
            ).first()
            if purchase is None:
                purchase = Purchase.objects.create(
                    business=business,
                    branch=branch,
                    supplier=supplier,
                    internal_number="DEMO-PUR-001",
                    purchase_date=timezone.localdate(),
                    supplier_reference="DEMO-SUPPLIER-INVOICE-001",
                    settlement_terms="Local demonstration only; no payable is recorded.",
                    created_by=owner_membership,
                )
                purchase_line = PurchaseLine.objects.create(
                    business=business,
                    purchase=purchase,
                    variant=demo_variants["TSHIRT-BLK-M"],
                    ordered_quantity=Decimal("8"),
                    unit_cost=Decimal("500"),
                )
                approve_purchase(actor=owner_membership, purchase=purchase)
                receipt = receive_purchase(
                    actor=owner_membership,
                    purchase=purchase,
                    quantities=[ReceiptQuantity(purchase_line.id, Decimal("8"))],
                    idempotency_key=uuid.UUID("00000000-0000-4000-8000-000000000201"),
                )
                receipt_line = receipt.lines.get()
                purchase_return = save_purchase_return_draft(
                    actor=owner_membership,
                    purchase=purchase,
                    return_date=timezone.localdate(),
                    reason="One damaged shirt returned in the local demo.",
                    supplier_document_reference="DEMO-RETURN-001",
                    quantities=[PurchaseReturnQuantity(receipt_line.id, Decimal("1"))],
                )
                post_purchase_return(
                    actor=owner_membership,
                    purchase_return=purchase_return,
                    idempotency_key=uuid.UUID("00000000-0000-4000-8000-000000000202"),
                )
            else:
                purchase_return = purchase.purchase_returns.get(
                    supplier_document_reference="DEMO-RETURN-001"
                )
            sale_key = uuid.UUID("00000000-0000-4000-8000-000000000203")
            posting_key = SalePostingKey.objects.filter(
                business=business,
                key=sale_key,
            ).first()
            if posting_key is None:
                sale = save_sale_draft(
                    actor=cashier_membership,
                    branch=branch,
                    sale_date=timezone.localdate(),
                    quantities=[
                        SaleQuantity(
                            demo_variants["TSHIRT-BLK-M"].id,
                            Decimal("1"),
                        )
                    ],
                )
                sale = post_sale(
                    actor=cashier_membership,
                    sale=sale,
                    payment_method="cash",
                    telebirr_reference="",
                    idempotency_key=sale_key,
                )
            else:
                sale = Sale.objects.get(
                    business=business,
                    posting_key=posting_key,
                )
            return_key = uuid.UUID("00000000-0000-4000-8000-000000000204")
            if not SaleReturnPostingKey.objects.filter(
                business=business,
                key=return_key,
            ).exists():
                sale_line = sale.lines.get()
                sale_return = save_sale_return_draft(
                    actor=cashier_membership,
                    sale=sale,
                    purpose=SaleReturnPurpose.CUSTOMER_RETURN,
                    return_date=timezone.localdate(),
                    reason="One saleable shirt returned in the local demo.",
                    quantities=[SaleReturnQuantity(sale_line.id, Decimal("1"))],
                )
                sale_return = post_sale_return(
                    actor=owner_membership,
                    sale_return=sale_return,
                    refund_method="cash",
                    telebirr_reference="",
                    idempotency_key=return_key,
                )
            else:
                sale_return = sale.returns.get(posting_key__key=return_key)
            expense_category, _ = ExpenseCategory.objects.update_or_create(
                business=business,
                name="Utilities",
                defaults={"is_active": True},
            )
            expense_key = uuid.UUID("00000000-0000-4000-8000-000000000209")
            expense_posting_key = ExpenseSettlementPostingKey.objects.filter(
                business=business,
                key=expense_key,
            ).first()
            if expense_posting_key is None:
                operating_expense = create_operating_expense_draft(
                    actor=owner_membership,
                    branch=branch,
                    category=expense_category,
                    payee="Demo Internet Provider",
                    description="Local demonstration operating expense.",
                    amount=Decimal("150.00"),
                )
                operating_expense = post_operating_expense(
                    actor=owner_membership,
                    expense=operating_expense,
                    method=OperationalPaymentMethod.TELEBIRR,
                    telebirr_reference="DEMO-TX-EXPENSE-001",
                    idempotency_key=expense_key,
                )
            else:
                operating_expense = OperatingExpense.objects.get(
                    business=business,
                    posting_key=expense_posting_key,
                )
            supplier_payment_key = uuid.UUID("00000000-0000-4000-8000-000000000210")
            supplier_payment_posting_key = ExpenseSettlementPostingKey.objects.filter(
                business=business,
                key=supplier_payment_key,
            ).first()
            if supplier_payment_posting_key is None:
                supplier_payment = post_supplier_payment(
                    actor=owner_membership,
                    purchase=purchase,
                    amount=Decimal("1000.00"),
                    method=OperationalPaymentMethod.TELEBIRR,
                    supplier_reference="DEMO-SUPPLIER-PAYMENT-001",
                    telebirr_reference="DEMO-TX-SUPPLIER-001",
                    idempotency_key=supplier_payment_key,
                )
            else:
                supplier_payment = SupplierPayment.objects.get(
                    business=business,
                    posting_key=supplier_payment_posting_key,
                )
            settlement_key = uuid.UUID("00000000-0000-4000-8000-000000000211")
            settlement_posting_key = ExpenseSettlementPostingKey.objects.filter(
                business=business,
                key=settlement_key,
            ).first()
            if settlement_posting_key is None:
                supplier_settlement = post_supplier_return_settlement(
                    actor=owner_membership,
                    purchase_return=purchase_return,
                    settlement_type=SupplierReturnSettlementType.CREDIT,
                    amount=Decimal("300.00"),
                    method="",
                    supplier_reference="DEMO-SUPPLIER-CREDIT-001",
                    telebirr_reference="",
                    idempotency_key=settlement_key,
                )
            else:
                supplier_settlement = SupplierReturnSettlement.objects.get(
                    business=business,
                    posting_key=settlement_posting_key,
                )
            post_manual_cash_movement(
                actor=owner_membership,
                session=cash_session,
                movement_type=CashMovementType.CASH_ADDED,
                amount=Decimal("25.00"),
                reason="Added change to the local demo drawer.",
                idempotency_key=uuid.UUID("00000000-0000-4000-8000-000000000207"),
            )
            post_manual_cash_movement(
                actor=owner_membership,
                session=cash_session,
                movement_type=CashMovementType.CASH_REMOVED,
                amount=Decimal("25.00"),
                reason="Removed the demonstration change from the drawer.",
                idempotency_key=uuid.UUID("00000000-0000-4000-8000-000000000208"),
            )
            closure = close_cash_session(
                actor=cashier_membership,
                session=cash_session,
                actual_cash=Decimal("500.00"),
                explanation="",
                idempotency_key=uuid.UUID("00000000-0000-4000-8000-000000000206"),
            )

        self.stdout.write(self.style.SUCCESS("Local demo data is ready."))
        self.stdout.write("Owner: owner@demo.ife.local")
        self.stdout.write("Cashier: cashier@demo.ife.local")
        self.stdout.write("Posted purchase: DEMO-PUR-001")
        self.stdout.write("Posted supplier return: DEMO-RETURN-001")
        self.stdout.write(f"Posted cash sale: {sale.internal_number}")
        self.stdout.write(f"Internal receipt: {sale.receipt.internal_number}")
        self.stdout.write(f"Posted customer return: {sale_return.internal_number}")
        self.stdout.write(f"Internal return receipt: {sale_return.receipt.internal_number}")
        self.stdout.write(f"Posted operating expense: {operating_expense.internal_number}")
        self.stdout.write(f"Supplier payment: {supplier_payment.id}")
        self.stdout.write(f"Supplier return credit: {supplier_settlement.id}")
        self.stdout.write(f"Closed cash session: {closure.session.business_date}")

    @staticmethod
    def _upsert_user(*, email: str, full_name: str, password: str) -> User:
        user, _ = User.objects.update_or_create(
            email=email,
            defaults={"full_name": full_name, "is_active": True},
        )
        user.set_password(password)
        user.save(update_fields=("password",))
        return user
