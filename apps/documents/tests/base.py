from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.documents.models import DocumentFile
from apps.expenses.models import ExpenseCategory
from apps.purchasing.models import Supplier


class DocumentTestMixin(TestCase):
    business: Business
    branch: Branch
    owner: BusinessMembership
    manager: BusinessMembership
    cashier: BusinessMembership
    stock_employee: BusinessMembership
    supplier: Supplier
    category: ExpenseCategory
    variant: ProductVariant
    second_variant: ProductVariant

    def setUp(self) -> None:
        self.business = Business.objects.create(
            name="Stage 8 Clothing",
            slug="stage-8-clothing",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main Shop",
            code="main",
        )
        self.owner = self._membership("owner@example.com", MembershipRole.OWNER)
        self.manager = self._membership("manager@example.com", MembershipRole.MANAGER)
        self.cashier = self._membership("cashier@example.com", MembershipRole.CASHIER)
        self.stock_employee = self._membership(
            "stock@example.com",
            MembershipRole.STOCK_EMPLOYEE,
        )
        self.supplier = Supplier.objects.create(
            business=self.business,
            name="Addis Garment Supplier",
        )
        self.category = ExpenseCategory.objects.create(
            business=self.business,
            name="Transport",
        )
        product = Product.objects.create(
            business=self.business,
            name="Cotton Shirt",
        )
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHIRT-BLK-M",
            size="M",
            color="Black",
            selling_price=Decimal("900.00"),
            stock_unit=StockUnit.PIECE,
        )
        self.second_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHIRT-WHT-L",
            size="L",
            color="White",
            selling_price=Decimal("950.00"),
            stock_unit=StockUnit.PIECE,
        )

    def tearDown(self) -> None:
        for document_file in DocumentFile.objects.filter(purged_at__isnull=True):
            document_file.source.storage.delete(document_file.source.name)

    def _membership(self, email: str, role: str) -> BusinessMembership:
        user = User.objects.create_user(email=email, password="strong-test-password")
        return BusinessMembership.objects.create(
            business=self.business,
            user=user,
            assigned_branch=self.branch,
            role=role,
        )

    def png_upload(
        self,
        *,
        name: str = "source.png",
        suffix: bytes = b"stage-eight",
    ) -> SimpleUploadedFile:
        return SimpleUploadedFile(
            name,
            b"\x89PNG\r\n\x1a\n" + suffix,
            content_type="image/png",
        )
