import uuid
from collections.abc import Iterable

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.sales.models import Sale


class OfflineSaleSyncStatus(models.TextChoices):
    SYNCED = "synced", _("Synced")
    NEEDS_REVIEW = "needs_review", _("Needs review")
    REJECTED = "rejected", _("Rejected")


class OfflineSaleSyncKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="offline_sale_sync_keys",
    )
    key = models.UUIDField()
    local_draft_id = models.UUIDField()
    payload_hash = models.CharField(
        max_length=64,
        validators=[
            RegexValidator(
                regex=r"^[0-9a-f]{64}$",
                message=_("Offline payload hashes must be lowercase SHA-256 values."),
            )
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "key"),
                name="offline_unique_sale_sync_key_per_business",
            ),
            models.UniqueConstraint(
                fields=("business", "local_draft_id"),
                name="offline_unique_local_sale_draft_per_business",
            ),
        ]

    def __str__(self) -> str:
        return str(self.key)

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Offline sale sync keys cannot be modified."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Offline sale sync keys cannot be deleted."))


class OfflineSaleSync(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="offline_sale_syncs",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="offline_sale_syncs",
    )
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="offline_sale_syncs",
    )
    sync_key = models.OneToOneField(
        OfflineSaleSyncKey,
        on_delete=models.PROTECT,
        related_name="result",
    )
    sale = models.OneToOneField(
        Sale,
        on_delete=models.PROTECT,
        related_name="offline_sync",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=OfflineSaleSyncStatus.choices,
    )
    offline_created_at = models.DateTimeField()
    synced_at = models.DateTimeField()
    snapshot = models.JSONField(default=dict)
    conflict_messages = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-synced_at", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=OfflineSaleSyncStatus.values),
                name="offline_sale_sync_status_is_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status__in=(
                            OfflineSaleSyncStatus.SYNCED,
                            OfflineSaleSyncStatus.NEEDS_REVIEW,
                        ),
                        sale__isnull=False,
                    )
                    | Q(status=OfflineSaleSyncStatus.REJECTED, sale__isnull=True)
                ),
                name="offline_sale_sync_sale_matches_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sync_key.local_draft_id} — {self.get_status_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Offline sale sync evidence cannot be modified."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Offline sale sync evidence cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        if self.branch_id and self.branch.business_id != self.business_id:
            raise ValidationError({"branch": _("The branch must belong to this business.")})
        if self.actor_id and self.actor.business_id != self.business_id:
            raise ValidationError({"actor": _("The actor must belong to this business.")})
        if self.sync_key_id and self.sync_key.business_id != self.business_id:
            raise ValidationError({"sync_key": _("The sync key must belong to this business.")})
        sale = self.sale if self.sale_id else None
        if sale is not None and (
            sale.business_id != self.business_id or sale.branch_id != self.branch_id
        ):
            raise ValidationError(
                {"sale": _("The synchronized sale must belong to this business and branch.")}
            )
