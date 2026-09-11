from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.accounts.models import User
from apps.businesses.models import (
    Branch,
    Business,
    BusinessMembership,
    BusinessType,
    MembershipRole,
)
from apps.catalog.models import Category, Product, ProductVariant


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
            BusinessMembership.objects.update_or_create(
                business=business,
                user=owner,
                defaults={
                    "assigned_branch": branch,
                    "role": MembershipRole.OWNER,
                    "is_active": True,
                },
            )
            BusinessMembership.objects.update_or_create(
                business=business,
                user=cashier,
                defaults={
                    "assigned_branch": branch,
                    "role": MembershipRole.CASHIER,
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
                (shirt, "TSHIRT-BLK-M", "M", "Black", Decimal("850.00"), Decimal("520.00")),
                (shirt, "TSHIRT-WHT-L", "L", "White", Decimal("850.00"), Decimal("520.00")),
                (shoes, "SHOE-BRN-42", "42", "Brown", Decimal("3200.00"), Decimal("2100.00")),
                (shoes, "SHOE-BLK-43", "43", "Black", Decimal("3200.00"), Decimal("2100.00")),
            )
            for product, sku, size, color, selling_price, cost_price in variants:
                ProductVariant.objects.update_or_create(
                    business=business,
                    sku=sku,
                    defaults={
                        "product": product,
                        "size": size,
                        "color": color,
                        "selling_price": selling_price,
                        "cost_price": cost_price,
                        "is_active": True,
                    },
                )

        self.stdout.write(self.style.SUCCESS("Local demo data is ready."))
        self.stdout.write("Owner: owner@demo.ife.local")
        self.stdout.write("Cashier: cashier@demo.ife.local")

    @staticmethod
    def _upsert_user(*, email: str, full_name: str, password: str) -> User:
        user, _ = User.objects.update_or_create(
            email=email,
            defaults={"full_name": full_name, "is_active": True},
        )
        user.set_password(password)
        user.save(update_fields=("password",))
        return user
