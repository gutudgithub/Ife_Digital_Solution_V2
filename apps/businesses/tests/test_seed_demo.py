from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Category, Product, ProductVariant
from apps.purchasing.models import Supplier


@override_settings(DEBUG=True)
class SeedDemoCommandTests(TestCase):
    password = "LocalDemo123!"

    def test_seed_demo_creates_idempotent_tenant_data(self) -> None:
        call_command("seed_demo", password=self.password)
        call_command("seed_demo", password=self.password)

        business = Business.objects.get(slug="ife-demo-fashion")
        owner = User.objects.get(email="owner@demo.ife.local")
        cashier = User.objects.get(email="cashier@demo.ife.local")
        stock_employee = User.objects.get(email="stock@demo.ife.local")

        self.assertTrue(owner.check_password(self.password))
        self.assertTrue(cashier.check_password(self.password))
        self.assertTrue(stock_employee.check_password(self.password))
        self.assertEqual(Branch.objects.filter(business=business).count(), 1)
        self.assertEqual(Category.objects.filter(business=business).count(), 2)
        self.assertEqual(Product.objects.filter(business=business).count(), 2)
        self.assertEqual(ProductVariant.objects.filter(business=business).count(), 4)
        self.assertEqual(BusinessMembership.objects.filter(business=business).count(), 3)
        self.assertEqual(Supplier.objects.filter(business=business).count(), 1)
        self.assertEqual(
            (
                BusinessMembership.objects.get(business=business, user=owner).role,
                BusinessMembership.objects.get(
                    business=business,
                    user=owner,
                ).assigned_branch,
            ),
            (MembershipRole.OWNER, Branch.objects.get(business=business)),
        )
        self.assertEqual(
            (
                BusinessMembership.objects.get(business=business, user=cashier).role,
                BusinessMembership.objects.get(
                    business=business,
                    user=cashier,
                ).assigned_branch,
            ),
            (MembershipRole.CASHIER, Branch.objects.get(business=business)),
        )
        self.assertEqual(
            (
                BusinessMembership.objects.get(
                    business=business,
                    user=stock_employee,
                ).role,
                BusinessMembership.objects.get(
                    business=business,
                    user=stock_employee,
                ).assigned_branch,
            ),
            (MembershipRole.STOCK_EMPLOYEE, Branch.objects.get(business=business)),
        )

    @override_settings(DEBUG=False)
    def test_seed_demo_is_disabled_outside_debug_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "only be created"):
            call_command("seed_demo", password=self.password)
