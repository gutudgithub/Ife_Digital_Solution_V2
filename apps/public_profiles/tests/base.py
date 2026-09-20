from decimal import Decimal

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Category, Product, ProductVariant, StockUnit
from apps.public_profiles.models import PublicBusinessProfile
from apps.public_profiles.services import (
    ProfileDraftData,
    get_or_create_profile,
    publish_profile,
    set_product_publication,
    update_profile,
)


@override_settings(PUBLIC_SITE_ORIGIN="https://public.example.test")
class PublicProfileTestMixin(TestCase):
    password = "strong-test-password"
    business: Business
    branch: Branch
    owner_user: User
    manager_user: User
    cashier_user: User
    stock_user: User
    staff_user: User
    owner: BusinessMembership
    manager: BusinessMembership
    cashier: BusinessMembership
    stock: BusinessMembership
    profile: PublicBusinessProfile
    product: Product
    variant: ProductVariant

    def setUp(self) -> None:
        self.business = Business.objects.create(
            name="Public Test Shop",
            slug="public-test-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner_user = self._user("owner")
        self.manager_user = self._user("manager")
        self.cashier_user = self._user("cashier")
        self.stock_user = self._user("stock")
        self.staff_user = self._user("staff", is_staff=True)
        self.owner = self._membership(self.owner_user, MembershipRole.OWNER)
        self.manager = self._membership(self.manager_user, MembershipRole.MANAGER)
        self.cashier = self._membership(self.cashier_user, MembershipRole.CASHIER)
        self.stock = self._membership(self.stock_user, MembershipRole.STOCK_EMPLOYEE)
        category = Category.objects.create(
            business=self.business,
            name="Footwear",
            slug="footwear",
        )
        self.product = Product.objects.create(
            business=self.business,
            category=category,
            name="Leather Shoe",
            description="Durable public product description.",
        )
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=self.product,
            sku="PRIVATE-SKU-42",
            size="42",
            color="Brown",
            selling_price=Decimal("1500.00"),
            cost_price=Decimal("487.65"),
            stock_unit=StockUnit.PAIR,
            low_stock_threshold=Decimal("2"),
        )
        self.profile = get_or_create_profile(self.owner)

    def _user(self, label: str, *, is_staff: bool = False) -> User:
        return User.objects.create_user(
            email=f"public-{label}@example.com",
            password=self.password,
            full_name=f"Public {label.title()}",
            is_staff=is_staff,
        )

    def _membership(self, user: User, role: str) -> BusinessMembership:
        return BusinessMembership.objects.create(
            business=self.business,
            user=user,
            assigned_branch=self.branch,
            role=role,
        )

    def grant_staff_permissions(self) -> None:
        permissions = Permission.objects.filter(
            codename__in=(
                "review_public_verification",
                "suspend_public_profile",
            )
        )
        self.staff_user.user_permissions.add(*permissions)
        self.staff_user = User.objects.get(pk=self.staff_user.pk)

    def grant_staff_permission(self, codename: str) -> None:
        self.staff_user.user_permissions.add(Permission.objects.get(codename=codename))
        self.staff_user = User.objects.get(pk=self.staff_user.pk)

    def complete_profile(self) -> PublicBusinessProfile:
        self.profile = update_profile(
            actor=self.owner,
            profile=self.profile,
            data=ProfileDraftData(
                display_name="Public Test Shop",
                description="Clothing and footwear selected by the business.",
                phone="+251900000000",
                email="",
                website="https://example.com",
                address="Addis Ababa",
                map_url="",
                supported_languages=("en",),
            ),
        )
        return self.profile

    def publish(self, *, prices: bool = True) -> PublicBusinessProfile:
        self.complete_profile()
        set_product_publication(
            actor=self.owner,
            profile=self.profile,
            product=self.product,
            visible=True,
            show_public_prices=prices,
        )
        self.profile = publish_profile(actor=self.owner, profile=self.profile)
        return self.profile
