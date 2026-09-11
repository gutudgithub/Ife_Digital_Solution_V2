import uuid
from collections.abc import Iterable

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.base import ModelBase
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
    assigned_branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="assigned_memberships",
        blank=True,
        null=True,
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

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        # Normal instance writes validate model and cross-business invariants here.
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def clean(self) -> None:
        super().clean()
        branch = self.assigned_branch
        if branch is not None and branch.business_id != self.business_id:
            raise ValidationError(
                {"assigned_branch": _("The assigned branch must belong to this business.")}
            )

    @property
    def can_manage_catalog(self) -> bool:
        return self.role in {MembershipRole.OWNER, MembershipRole.MANAGER}

    @property
    def can_manage_attendance(self) -> bool:
        return self.role in {MembershipRole.OWNER, MembershipRole.MANAGER}
