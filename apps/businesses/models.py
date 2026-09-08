import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class BusinessType(models.TextChoices):
    GENERAL_RETAIL = "general_retail", _("General retail")
    CLOTHING_FOOTWEAR = "clothing_footwear", _("Clothing and footwear")


class Business(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    business_type = models.CharField(
        max_length=32,
        choices=BusinessType.choices,
        default=BusinessType.CLOTHING_FOOTWEAR,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "businesses"

    def __str__(self) -> str:
        return self.name


class Branch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=40)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("business", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "code"),
                name="businesses_unique_branch_code_per_business",
            )
        ]

    def __str__(self) -> str:
        return f"{self.business.name} — {self.name}"


class MembershipRole(models.TextChoices):
    OWNER = "owner", _("Owner")
    MANAGER = "manager", _("Manager")
    CASHIER = "cashier", _("Cashier")


class BusinessMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="business_memberships",
    )
    role = models.CharField(max_length=16, choices=MembershipRole.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("business", "user")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "user"),
                name="businesses_unique_membership_per_business_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.business} ({self.get_role_display()})"

    @property
    def can_manage_catalog(self) -> bool:
        return self.role in {MembershipRole.OWNER, MembershipRole.MANAGER}
