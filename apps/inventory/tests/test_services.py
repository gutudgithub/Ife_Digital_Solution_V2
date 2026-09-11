import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.models import InventoryBalance, InventoryMovement, StockOperation
from apps.inventory.services import post_inventory_adjustment, post_opening_balance


class InventoryServiceTests(TestCase):
    business: Business
    branch: Branch
    owner_membership: BusinessMembership
    stock_membership: BusinessMembership
    cashier_membership: BusinessMembership
    variant: ProductVariant
    weighted_variant: ProductVariant

    def setUp(self) -> None:
        owner = User.objects.create_user(
            email="inventory-owner@example.com",
            password="strong-test-password",
            full_name="Inventory Owner",
        )
        stock_employee = User.objects.create_user(
            email="stock@example.com",
            password="strong-test-password",
            full_name="Stock Employee",
        )
        cashier = User.objects.create_user(
            email="inventory-cashier@example.com",
            password="strong-test-password",
            full_name="Inventory Cashier",
        )
        self.business = Business.objects.create(name="Shoe Shop", slug="shoe-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main Store",
            code="main",
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.stock_membership = BusinessMembership.objects.create(
            business=self.business,
            user=stock_employee,
            assigned_branch=self.branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )
        self.cashier_membership = BusinessMembership.objects.create(
            business=self.business,
            user=cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        product = Product.objects.create(business=self.business, name="Leather Shoe")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHOE-BLK-42",
            size="42",
            color="Black",
            selling_price=Decimal("1500.00"),
            stock_unit=StockUnit.PAIR,
        )
        self.weighted_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHOE-BRN-42",
            size="42",
            color="Brown",
            selling_price=Decimal("1500.00"),
            stock_unit=StockUnit.PAIR,
        )

    def test_opening_balance_creates_one_immutable_movement_and_replays_once(self) -> None:
        key = uuid.uuid4()

        first = post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("12"),
            unit_cost=Decimal("500"),
            idempotency_key=key,
        )
        replay = post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("12"),
            unit_cost=Decimal("500"),
            idempotency_key=key,
        )

        self.assertEqual(first.id, replay.id)
        self.assertEqual(StockOperation.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)
        balance = InventoryBalance.objects.get()
        self.assertEqual(balance.quantity_on_hand, Decimal("12.000"))
        self.assertEqual(balance.average_unit_cost, Decimal("500.000000"))
        self.assertEqual(balance.inventory_value, Decimal("6000.000000"))

    def test_opening_balance_is_rejected_after_any_movement(self) -> None:
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("1"),
            unit_cost=Decimal("500"),
            idempotency_key=uuid.uuid4(),
        )

        with self.assertRaisesMessage(ValidationError, "before the first movement"):
            post_opening_balance(
                actor=self.owner_membership,
                branch=self.branch,
                variant=self.variant,
                quantity=Decimal("1"),
                unit_cost=Decimal("500"),
                idempotency_key=uuid.uuid4(),
            )

        self.assertEqual(StockOperation.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)

    def test_weighted_average_and_negative_adjustment_reconcile(self) -> None:
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.weighted_variant,
            quantity=Decimal("10"),
            unit_cost=Decimal("100"),
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.weighted_variant,
            operation_type="adjustment_in",
            quantity=Decimal("10"),
            unit_cost=Decimal("200"),
            reason="Counted supplier stock received before purchasing rollout",
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.weighted_variant,
            operation_type="adjustment_out",
            quantity=Decimal("5"),
            reason="Damaged pairs removed after manager inspection",
            idempotency_key=uuid.uuid4(),
        )

        balance = InventoryBalance.objects.get(variant=self.weighted_variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("15.000"))
        self.assertEqual(balance.average_unit_cost, Decimal("150.000000"))
        self.assertEqual(balance.inventory_value, Decimal("2250.000000"))
        movement = InventoryMovement.objects.filter(
            variant=self.weighted_variant,
            movement_type="adjustment_out",
        ).get()
        self.assertEqual(movement.unit_cost, Decimal("150.000000"))
        self.assertEqual(movement.value_delta, Decimal("-750.000000"))

    def test_outbound_subtracts_value_when_average_cost_rounds_above_true_average(
        self,
    ) -> None:
        measured_variant = ProductVariant.objects.create(
            business=self.business,
            product=self.variant.product,
            sku="LEATHER-GRAM",
            selling_price=Decimal("1.00"),
            stock_unit=StockUnit.GRAM,
        )
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=measured_variant,
            quantity=Decimal("4497.665"),
            unit_cost=Decimal("0.000001"),
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner_membership,
            branch=self.branch,
            variant=measured_variant,
            operation_type="adjustment_in",
            quantity=Decimal("3989.366"),
            unit_cost=Decimal("0"),
            reason="Free measured stock added after recount",
            idempotency_key=uuid.uuid4(),
        )

        post_inventory_adjustment(
            actor=self.owner_membership,
            branch=self.branch,
            variant=measured_variant,
            operation_type="adjustment_out",
            quantity=Decimal("954.806"),
            reason="Measured stock removed after manager inspection",
            idempotency_key=uuid.uuid4(),
        )

        balance = InventoryBalance.objects.get(variant=measured_variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("7532.225"))
        self.assertEqual(balance.average_unit_cost, Decimal("0.000001"))
        self.assertEqual(balance.inventory_value, Decimal("0.003543"))
        movement = InventoryMovement.objects.filter(
            variant=measured_variant,
            movement_type="adjustment_out",
        ).get()
        self.assertEqual(movement.value_delta, Decimal("-0.000955"))
        self.assertEqual(
            sum(
                InventoryMovement.objects.filter(variant=measured_variant).values_list(
                    "value_delta",
                    flat=True,
                ),
                Decimal("0.000000"),
            ),
            balance.inventory_value,
        )

    def test_constraint_names_are_translated_during_posting(self) -> None:
        raw_error = ValidationError(
            'Constraint "inventory_movement_value_direction_matches" is violated.'
        )

        with (
            patch(
                "apps.inventory.services.InventoryMovement.objects.create",
                side_effect=raw_error,
            ),
            self.assertRaisesMessage(ValidationError, "inventory rule") as raised,
        ):
            post_opening_balance(
                actor=self.owner_membership,
                branch=self.branch,
                variant=self.variant,
                quantity=Decimal("1"),
                unit_cost=Decimal("500"),
                idempotency_key=uuid.uuid4(),
            )

        self.assertIs(raised.exception.__cause__, raw_error)
        self.assertFalse(InventoryBalance.objects.exists())
        self.assertFalse(StockOperation.objects.exists())

    def test_negative_adjustment_rolls_back_all_effects(self) -> None:
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("2"),
            unit_cost=Decimal("500"),
            idempotency_key=uuid.uuid4(),
        )

        with self.assertRaisesMessage(ValidationError, "make stock negative"):
            post_inventory_adjustment(
                actor=self.owner_membership,
                branch=self.branch,
                variant=self.variant,
                operation_type="adjustment_out",
                quantity=Decimal("3"),
                reason="Invalid over-adjustment",
                idempotency_key=uuid.uuid4(),
            )

        balance = InventoryBalance.objects.get(variant=self.variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("2.000"))
        self.assertEqual(StockOperation.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)

    def test_piece_pair_and_pack_units_reject_fractional_quantities(self) -> None:
        with self.assertRaisesMessage(ValidationError, "whole-number"):
            post_opening_balance(
                actor=self.owner_membership,
                branch=self.branch,
                variant=self.variant,
                quantity=Decimal("1.5"),
                unit_cost=Decimal("500"),
                idempotency_key=uuid.uuid4(),
            )

        self.assertFalse(InventoryBalance.objects.exists())

    def test_cashier_and_stock_employee_cannot_adjust_inventory(self) -> None:
        for actor in (self.cashier_membership, self.stock_membership):
            with self.assertRaises(PermissionDenied):
                post_inventory_adjustment(
                    actor=actor,
                    branch=self.branch,
                    variant=self.variant,
                    operation_type="adjustment_in",
                    quantity=Decimal("1"),
                    unit_cost=Decimal("500"),
                    reason="Unauthorized adjustment",
                    idempotency_key=uuid.uuid4(),
                )

    def test_cross_business_variant_is_rejected_without_disclosure(self) -> None:
        other_business = Business.objects.create(name="Other Shop", slug="other-shop")
        other_product = Product.objects.create(business=other_business, name="Other Product")
        other_variant = ProductVariant.objects.create(
            business=other_business,
            product=other_product,
            sku="OTHER-1",
            selling_price=Decimal("10.00"),
        )

        with self.assertRaises(ValidationError):
            post_opening_balance(
                actor=self.owner_membership,
                branch=self.branch,
                variant=other_variant,
                quantity=Decimal("1"),
                unit_cost=Decimal("5"),
                idempotency_key=uuid.uuid4(),
            )

        self.assertFalse(InventoryMovement.objects.exists())

    def test_posted_operation_and_movement_reject_instance_mutation_and_deletion(self) -> None:
        operation = post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("1"),
            unit_cost=Decimal("500"),
            idempotency_key=uuid.uuid4(),
        )
        movement = InventoryMovement.objects.get()

        operation.reason = "Changed"
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            operation.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            operation.delete()
        movement.reason = "Changed"
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            movement.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            movement.delete()

    def test_zero_balance_requires_zero_cost_and_value(self) -> None:
        balance = InventoryBalance(
            business=self.business,
            branch=self.branch,
            variant=self.variant,
            quantity_on_hand=Decimal("0.000"),
            average_unit_cost=Decimal("1.000000"),
            inventory_value=Decimal("1.000000"),
        )

        with self.assertRaises(ValidationError):
            balance.save()

    def test_reused_idempotency_key_for_another_variant_is_rejected(self) -> None:
        key = uuid.uuid4()
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("1"),
            unit_cost=Decimal("500"),
            idempotency_key=key,
        )

        with self.assertRaisesMessage(ValidationError, "another stock operation"):
            post_opening_balance(
                actor=self.owner_membership,
                branch=self.branch,
                variant=self.weighted_variant,
                quantity=Decimal("1"),
                unit_cost=Decimal("500"),
                idempotency_key=key,
            )
