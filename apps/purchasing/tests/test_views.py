import uuid
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryMovementType,
    InventorySourceType,
)
from apps.inventory.services import post_inventory_adjustment, post_opening_balance
from apps.purchasing.models import (
    GoodsReceiptLine,
    Purchase,
    PurchaseLine,
    PurchaseReturn,
    PurchaseReturnStatus,
    Supplier,
)
from apps.purchasing.services import (
    ReceiptQuantity,
    ReturnQuantity,
    approve_purchase,
    post_purchase_return,
    receive_purchase,
    save_purchase_return_draft,
)


class StageTwoViewTests(TestCase):
    owner: User
    cashier: User
    stock_employee: User
    business: Business
    branch: Branch
    owner_membership: BusinessMembership
    supplier: Supplier
    purchase: Purchase
    variant: ProductVariant

    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="view-owner@example.com",
            password="strong-test-password",
            full_name="View Owner",
        )
        self.cashier = User.objects.create_user(
            email="view-cashier@example.com",
            password="strong-test-password",
            full_name="View Cashier",
        )
        self.stock_employee = User.objects.create_user(
            email="view-stock@example.com",
            password="strong-test-password",
            full_name="View Stock",
        )
        self.business = Business.objects.create(name="View Shop", slug="view-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=self.owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.stock_employee,
            assigned_branch=self.branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )
        self.supplier = Supplier.objects.create(
            business=self.business,
            name="Private Supplier",
            phone="+251900000000",
        )
        product = Product.objects.create(business=self.business, name="Dress Shoe")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="DRESS-42",
            selling_price=Decimal("1700"),
        )
        self.purchase = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=self.supplier,
            internal_number="PUR-VIEW",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        PurchaseLine.objects.create(
            business=self.business,
            purchase=self.purchase,
            variant=self.variant,
            ordered_quantity=Decimal("2"),
            unit_cost=Decimal("700"),
        )

    def test_cashier_cannot_access_supplier_or_purchase_cost_pages(self) -> None:
        client = Client()
        client.force_login(self.cashier)

        supplier_response = client.get(reverse("purchasing:supplier-list"))
        purchase_response = client.get(reverse("purchasing:purchase-list"))

        self.assertEqual(supplier_response.status_code, 403)
        self.assertEqual(purchase_response.status_code, 403)

    def test_supplier_create_rejects_duplicate_name_without_server_error(self) -> None:
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("purchasing:supplier-create"),
            {
                "name": self.supplier.name.lower(),
                "phone": "",
                "email": "",
                "address": "",
                "notes": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A supplier with this name already exists.")
        self.assertEqual(Supplier.objects.filter(business=self.business).count(), 1)

    def test_supplier_edit_rejects_duplicate_name_without_server_error(self) -> None:
        second_supplier = Supplier.objects.create(
            business=self.business,
            name="Second Supplier",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("purchasing:supplier-edit", args=[second_supplier.id]),
            {
                "name": self.supplier.name.lower(),
                "phone": "",
                "email": "",
                "address": "",
                "notes": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A supplier with this name already exists.")
        second_supplier.refresh_from_db()
        self.assertEqual(second_supplier.name, "Second Supplier")

    def test_stock_employee_can_view_purchases_but_cannot_create_or_approve(self) -> None:
        client = Client()
        client.force_login(self.stock_employee)

        list_response = client.get(reverse("purchasing:purchase-list"))

        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, self.purchase.internal_number)
        self.assertEqual(
            client.get(reverse("purchasing:purchase-detail", args=[self.purchase.id])).status_code,
            404,
        )
        self.assertEqual(client.get(reverse("purchasing:purchase-create")).status_code, 403)
        self.assertEqual(
            client.post(
                reverse("purchasing:purchase-approve", args=[self.purchase.id])
            ).status_code,
            403,
        )
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)

        approved_list_response = client.get(reverse("purchasing:purchase-list"))

        self.assertContains(approved_list_response, self.purchase.internal_number)
        self.assertEqual(
            client.get(reverse("purchasing:purchase-detail", args=[self.purchase.id])).status_code,
            200,
        )

    def test_cross_business_purchase_url_returns_not_found(self) -> None:
        other_business = Business.objects.create(name="Hidden", slug="hidden")
        other_branch = Branch.objects.create(
            business=other_business,
            name="Hidden",
            code="hidden",
        )
        other_supplier = Supplier.objects.create(business=other_business, name="Hidden Supplier")
        other_user = User.objects.create_user(
            email="hidden-owner@example.com",
            password="strong-test-password",
            full_name="Hidden Owner",
        )
        other_membership = BusinessMembership.objects.create(
            business=other_business,
            user=other_user,
            role=MembershipRole.OWNER,
        )
        hidden_purchase = Purchase.objects.create(
            business=other_business,
            branch=other_branch,
            supplier=other_supplier,
            internal_number="PUR-HIDDEN",
            purchase_date=timezone.localdate(),
            created_by=other_membership,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("purchasing:purchase-detail", args=[hidden_purchase.id]))

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "PUR-HIDDEN", status_code=404)

    def test_cashier_inventory_page_hides_cost_and_value(self) -> None:
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("2"),
            unit_cost=Decimal("700"),
            idempotency_key=uuid.uuid4(),
        )
        client = Client()
        client.force_login(self.cashier)

        response = client.get(reverse("inventory:inventory-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2.000")
        self.assertNotContains(response, "Average cost")
        self.assertNotContains(response, "Inventory value")
        self.assertNotContains(response, "700.00")
        self.assertNotContains(response, "Recent movements")
        self.assertNotContains(response, self.owner.full_name)

    def test_inventory_balances_are_paginated(self) -> None:
        for index in range(51):
            variant = ProductVariant.objects.create(
                business=self.business,
                product=self.variant.product,
                sku=f"PAGE-{index:02d}",
                selling_price=Decimal("100"),
            )
            InventoryBalance.objects.create(
                business=self.business,
                branch=self.branch,
                variant=variant,
            )
        self.client.force_login(self.owner)

        first_page = self.client.get(reverse("inventory:inventory-list"))
        second_page = self.client.get(
            reverse("inventory:inventory-list"),
            {"page": "2"},
        )

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(len(first_page.context["balances"]), 50)
        self.assertContains(first_page, "?page=2")
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(second_page.context["balances"]), 1)

    def test_owner_can_post_opening_balance_from_scoped_form(self) -> None:
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("inventory:opening-create"),
            {
                "branch": str(self.branch.id),
                "variant": str(self.variant.id),
                "quantity": "3",
                "unit_cost": "650",
                "idempotency_key": "b0b50e69-420e-4420-b896-854665127371",
            },
        )

        self.assertRedirects(response, reverse("inventory:inventory-list"))
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.variant).quantity_on_hand,
            Decimal("3.000"),
        )

    def test_inventory_form_rejects_cross_business_variant(self) -> None:
        other_business = Business.objects.create(name="Other Form", slug="other-form")
        other_product = Product.objects.create(business=other_business, name="Other Shoe")
        other_variant = ProductVariant.objects.create(
            business=other_business,
            product=other_product,
            sku="OTHER-VIEW",
            selling_price=Decimal("100"),
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("inventory:opening-create"),
            {
                "branch": str(self.branch.id),
                "variant": str(other_variant.id),
                "quantity": "1",
                "unit_cost": "50",
                "idempotency_key": "00577250-a8bb-433e-b21d-27f720735b9b",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(InventoryBalance.objects.exists())

    def test_owner_create_approve_and_stock_employee_receive_workflow(self) -> None:
        self.client.force_login(self.owner)
        create_response = self.client.post(
            reverse("purchasing:purchase-create"),
            {
                "branch": str(self.branch.id),
                "supplier": str(self.supplier.id),
                "purchase_date": str(timezone.localdate()),
                "expected_date": "",
                "supplier_reference": "SUP-INV-42",
                "settlement_terms": "Settlement tracked outside Stage 2A",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-variant": str(self.variant.id),
                "lines-0-ordered_quantity": "2",
                "lines-0-unit_cost": "700",
            },
        )

        self.assertEqual(create_response.status_code, 302)
        created_purchase = Purchase.objects.exclude(pk=self.purchase.pk).get()
        self.assertRedirects(
            create_response,
            reverse("purchasing:purchase-detail", args=[created_purchase.id]),
        )
        approve_response = self.client.post(
            reverse("purchasing:purchase-approve", args=[created_purchase.id])
        )
        self.assertRedirects(
            approve_response,
            reverse("purchasing:purchase-detail", args=[created_purchase.id]),
        )
        created_line = created_purchase.lines.get()
        stock_client = Client()
        stock_client.force_login(self.stock_employee)

        receive_response = stock_client.post(
            reverse("purchasing:purchase-receive", args=[created_purchase.id]),
            {
                "idempotency_key": "468414fd-6c78-4639-9335-6fd93e5b9113",
                "supplier_document_reference": "DELIVERY-42",
                f"quantity_{created_line.id.hex}": "2",
            },
        )

        self.assertRedirects(
            receive_response,
            reverse("purchasing:purchase-detail", args=[created_purchase.id]),
        )
        created_purchase.refresh_from_db()
        self.assertEqual(created_purchase.status, "received")
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.variant).quantity_on_hand,
            Decimal("2.000"),
        )

    def test_purchase_form_reports_nonpositive_quantity_without_constraint_name(self) -> None:
        self.client.force_login(self.owner)

        for quantity in ("0", "-1"):
            response = self.client.post(
                reverse("purchasing:purchase-create"),
                {
                    "branch": str(self.branch.id),
                    "supplier": str(self.supplier.id),
                    "purchase_date": str(timezone.localdate()),
                    "expected_date": "",
                    "supplier_reference": "",
                    "settlement_terms": "",
                    "lines-TOTAL_FORMS": "1",
                    "lines-INITIAL_FORMS": "0",
                    "lines-MIN_NUM_FORMS": "1",
                    "lines-MAX_NUM_FORMS": "1000",
                    "lines-0-variant": str(self.variant.id),
                    "lines-0-ordered_quantity": quantity,
                    "lines-0-unit_cost": "700",
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Ordered quantity must be greater than zero.")
            self.assertNotContains(response, "purchasing_line_quantity_positive")
        self.assertEqual(Purchase.objects.count(), 1)

    def _receive_return_source(
        self,
        quantity: Decimal = Decimal("2"),
    ) -> GoodsReceiptLine:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        purchase_line = self.purchase.lines.get()
        receipt = receive_purchase(
            actor=self.owner_membership,
            purchase=self.purchase,
            quantities=[ReceiptQuantity(purchase_line.id, quantity)],
            idempotency_key=uuid.uuid4(),
        )
        return receipt.lines.get()

    def test_owner_can_create_post_and_reverse_purchase_return(self) -> None:
        receipt_line = self._receive_return_source()
        self.client.force_login(self.owner)

        create_response = self.client.post(
            reverse("purchasing:purchase-return-create", args=[self.purchase.id]),
            {
                "return_date": str(timezone.localdate()),
                "reason": "Wrong size delivered",
                "supplier_document_reference": "SUP-RMA-7",
                f"quantity_{receipt_line.id.hex}": "1",
            },
        )

        purchase_return = PurchaseReturn.objects.get()
        self.assertRedirects(
            create_response,
            reverse("purchasing:purchase-return-detail", args=[purchase_return.id]),
        )
        post_response = self.client.post(
            reverse("purchasing:purchase-return-post", args=[purchase_return.id]),
            {"idempotency_key": str(uuid.uuid4())},
        )
        self.assertRedirects(
            post_response,
            reverse("purchasing:purchase-return-detail", args=[purchase_return.id]),
        )
        purchase_return.refresh_from_db()
        self.assertEqual(purchase_return.status, PurchaseReturnStatus.POSTED)
        detail_response = self.client.get(
            reverse("purchasing:purchase-return-detail", args=[purchase_return.id])
        )
        self.assertContains(detail_response, "Supplier reference amount")
        self.assertContains(detail_response, "Assigned inventory cost")
        self.assertContains(detail_response, "Inventory value reduction")
        reverse_response = self.client.post(
            reverse("purchasing:purchase-return-reverse", args=[purchase_return.id]),
            {
                "reason": "Return entered against wrong delivery",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        self.assertRedirects(
            reverse_response,
            reverse("purchasing:purchase-return-detail", args=[purchase_return.id]),
        )
        purchase_return.refresh_from_db()
        self.assertEqual(purchase_return.status, PurchaseReturnStatus.REVERSED)
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.variant).quantity_on_hand,
            Decimal("2.000"),
        )

    def test_stock_employee_can_prepare_but_not_post_and_inventory_values_are_hidden(
        self,
    ) -> None:
        receipt_line = self._receive_return_source()
        purchase_return = save_purchase_return_draft(
            actor=BusinessMembership.objects.get(user=self.stock_employee),
            purchase=self.purchase,
            return_date=timezone.localdate(),
            reason="Supplier return prepared by stock employee",
            quantities=[ReturnQuantity(receipt_line.id, Decimal("1"))],
        )
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=uuid.uuid4(),
        )
        client = Client()
        client.force_login(self.stock_employee)

        detail_response = client.get(
            reverse("purchasing:purchase-return-detail", args=[purchase_return.id])
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Supplier reference amount")
        self.assertContains(detail_response, "Receipt unit cost")
        self.assertNotContains(detail_response, "<th>Assigned inventory cost</th>", html=True)
        self.assertNotContains(
            detail_response,
            "<th>Inventory value reduction</th>",
            html=True,
        )
        self.assertEqual(
            client.post(
                reverse("purchasing:purchase-return-post", args=[purchase_return.id]),
                {"idempotency_key": str(uuid.uuid4())},
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("purchasing:purchase-return-reverse", args=[purchase_return.id]),
                {
                    "reason": "Unauthorized",
                    "idempotency_key": str(uuid.uuid4()),
                },
            ).status_code,
            403,
        )

    def test_cashier_cannot_access_purchase_return_or_cost_history_pages(self) -> None:
        receipt_line = self._receive_return_source()
        purchase_return = save_purchase_return_draft(
            actor=self.owner_membership,
            purchase=self.purchase,
            return_date=timezone.localdate(),
            reason="Private supplier return",
            quantities=[ReturnQuantity(receipt_line.id, Decimal("1"))],
        )
        client = Client()
        client.force_login(self.cashier)

        for url in (
            reverse("purchasing:purchase-return-list"),
            reverse("purchasing:purchase-return-detail", args=[purchase_return.id]),
            reverse("purchasing:purchase-return-create", args=[self.purchase.id]),
            reverse("purchasing:purchase-cost-history"),
        ):
            self.assertEqual(client.get(url).status_code, 403)

    def test_purchase_return_detail_is_tenant_scoped(self) -> None:
        receipt_line = self._receive_return_source()
        purchase_return = save_purchase_return_draft(
            actor=self.owner_membership,
            purchase=self.purchase,
            return_date=timezone.localdate(),
            reason="Tenant-private supplier return",
            quantities=[ReturnQuantity(receipt_line.id, Decimal("1"))],
        )
        other_business = Business.objects.create(name="Other View", slug="other-view")
        other_owner = User.objects.create_user(
            email="other-view-owner@example.com",
            password="strong-test-password",
            full_name="Other View Owner",
        )
        BusinessMembership.objects.create(
            business=other_business,
            user=other_owner,
            role=MembershipRole.OWNER,
        )
        client = Client()
        client.force_login(other_owner)

        response = client.get(
            reverse("purchasing:purchase-return-detail", args=[purchase_return.id])
        )

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, purchase_return.internal_number, status_code=404)

    def test_supplier_activity_and_purchase_cost_history_render_posted_evidence(
        self,
    ) -> None:
        receipt_line = self._receive_return_source()
        purchase_return = save_purchase_return_draft(
            actor=self.owner_membership,
            purchase=self.purchase,
            return_date=timezone.localdate(),
            reason="Activity summary return",
            quantities=[ReturnQuantity(receipt_line.id, Decimal("1"))],
        )
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.owner)

        supplier_response = self.client.get(reverse("purchasing:supplier-list"))
        cost_response = self.client.get(
            reverse("purchasing:purchase-cost-history"),
            {
                "supplier": str(self.supplier.id),
                "variant": str(self.variant.id),
                "date_from": str(timezone.localdate()),
                "date_to": str(timezone.localdate()),
            },
        )

        self.assertContains(supplier_response, "Posted returns")
        self.assertContains(supplier_response, "Return reference total")
        self.assertContains(supplier_response, "700.00")
        self.assertContains(cost_response, "Purchase cost history")
        self.assertContains(cost_response, self.variant.sku)
        self.assertContains(cost_response, "700.000000")
        self.assertContains(cost_response, "1400.00")

    def test_inventory_movement_history_filters_and_paginates_independently(self) -> None:
        for index in range(51):
            InventoryMovement.objects.create(
                business=self.business,
                branch=self.branch,
                variant=self.variant,
                movement_type=InventoryMovementType.ADJUSTMENT_IN,
                quantity_delta=Decimal("1"),
                unit_cost=Decimal("1"),
                value_delta=Decimal("1"),
                source_type=InventorySourceType.STOCK_OPERATION,
                source_id=uuid.uuid4(),
                actor=self.owner_membership,
                reason=f"History entry {index}",
                posted_at=timezone.now(),
            )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("inventory:inventory-list"),
            {
                "movement_type": InventoryMovementType.ADJUSTMENT_IN,
                "variant": str(self.variant.id),
                "date_from": str(timezone.localdate()),
                "date_to": str(timezone.localdate()),
                "movement_page": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["movements"]), 1)
        self.assertEqual(
            response.context["movements"][0].reason,
            "History entry 0",
        )
        self.assertContains(response, "Movement history")
        self.assertContains(response, "movement_page=1")
        self.assertContains(response, f"variant={self.variant.id}")

    def test_stock_employee_movement_history_hides_inventory_values(self) -> None:
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("2"),
            unit_cost=Decimal("700"),
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            operation_type="adjustment_out",
            quantity=Decimal("1"),
            reason="Movement history permission test",
            idempotency_key=uuid.uuid4(),
        )
        client = Client()
        client.force_login(self.stock_employee)

        response = client.get(reverse("inventory:inventory-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Movement history")
        self.assertNotContains(response, "Assigned unit cost")
        self.assertNotContains(response, "Value change")
