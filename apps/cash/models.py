import uuid
from collections.abc import Iterable
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership


class CashSessionStatus(models.TextChoices):
    OPEN = "open", _("Open")
    CLOSED = "closed", _("Closed")


class CashMovementType(models.TextChoices):
    OPENING_FLOAT = "opening_float", _("Opening float")
    CASH_SALE = "cash_sale", _("Cash sale")
    CASH_REFUND = "cash_refund", _("Cash refund")
    CASH_ADDED = "cash_added", _("Cash added")
    CASH_REMOVED = "cash_removed", _("Cash removed")
    OPERATING_EXPENSE = "operating_expense", _("Operating expense")
    OPERATING_EXPENSE_REVERSAL = (
        "operating_expense_reversal",
        _("Operating expense reversal"),
    )
    SUPPLIER_PAYMENT = "supplier_payment", _("Supplier payment")
    SUPPLIER_PAYMENT_REVERSAL = (
        "supplier_payment_reversal",
        _("Supplier payment reversal"),
    )
    SUPPLIER_REFUND = "supplier_refund", _("Supplier refund recovered")
    SUPPLIER_REFUND_REVERSAL = (
        "supplier_refund_reversal",
        _("Supplier refund reversal"),
    )


class CashOperationType(models.TextChoices):
    OPEN = "open", _("Session opening")
    MANUAL_MOVEMENT = "manual_movement", _("Manual cash movement")
    CLOSE = "close", _("Session closing")
    REOPEN = "reopen", _("Session reopening")


class CashPostingKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="cash_posting_keys",
    )
    key = models.UUIDField()
    operation_type = models.CharField(max_length=24, choices=CashOperationType.choices)
    source_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "key"),
                name="cash_unique_posting_key_per_business",
            ),
            models.CheckConstraint(
                condition=Q(operation_type__in=CashOperationType.values),
                name="cash_posting_operation_type_valid",
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
            raise ValidationError(_("Cash posting keys cannot be modified."))
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
        raise ValidationError(_("Cash posting keys cannot be deleted."))


class CashSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="cash_sessions",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="cash_sessions",
    )
    business_date = models.DateField()
    status = models.CharField(
        max_length=12,
        choices=CashSessionStatus.choices,
        default=CashSessionStatus.OPEN,
    )
    opening_float = models.DecimalField(max_digits=18, decimal_places=2)
    opening_basis_note = models.TextField(blank=True)
    opening_key = models.OneToOneField(
        CashPostingKey,
        on_delete=models.PROTECT,
        related_name="opened_session",
    )
    opened_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="cash_sessions_opened",
    )
    opened_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-business_date", "-opened_at")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "branch", "business_date"),
                name="cash_unique_session_per_branch_date",
            ),
            models.UniqueConstraint(
                fields=("business", "branch"),
                condition=Q(status=CashSessionStatus.OPEN),
                name="cash_unique_open_session_per_branch",
            ),
            models.CheckConstraint(
                condition=Q(status__in=CashSessionStatus.values),
                name="cash_session_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(opening_float__gte=Decimal("0.00")),
                name="cash_session_opening_float_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.branch} — {self.business_date}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            stored = CashSession.objects.get(pk=self.pk)
            immutable_fields = (
                "business_id",
                "branch_id",
                "business_date",
                "opening_float",
                "opening_basis_note",
                "opening_key_id",
                "opened_by_id",
                "opened_at",
            )
            if any(getattr(self, field) != getattr(stored, field) for field in immutable_fields):
                raise ValidationError(_("Cash session opening evidence cannot be modified."))
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
        raise ValidationError(_("Cash sessions cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.opening_key_id:
            if self.opening_key.business_id != self.business_id:
                errors["opening_key"] = ValidationError(
                    _("Opening key must belong to this business.")
                )
            elif (
                self.opening_key.operation_type != CashOperationType.OPEN
                or self.opening_key.source_id != self.branch_id
            ):
                errors["opening_key"] = ValidationError(
                    _("Opening key must identify this branch opening.")
                )
        if self.opened_by_id and self.opened_by.business_id != self.business_id:
            errors["opened_by"] = ValidationError(_("Opening actor must belong to this business."))
        if errors:
            raise ValidationError(errors)


class CashMovement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="cash_movements",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="cash_movements",
    )
    session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    movement_type = models.CharField(max_length=32, choices=CashMovementType.choices)
    amount_delta = models.DecimalField(max_digits=18, decimal_places=2)
    source_id = models.UUIDField(blank=True, null=True)
    posting_key = models.OneToOneField(
        CashPostingKey,
        on_delete=models.PROTECT,
        related_name="manual_cash_movement",
        blank=True,
        null=True,
    )
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="cash_movements_posted",
    )
    reason = models.TextField(blank=True)
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("posted_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(movement_type__in=CashMovementType.values),
                name="cash_movement_type_valid",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(
                            movement_type=CashMovementType.OPENING_FLOAT,
                            amount_delta__gte=Decimal("0.00"),
                            source_id__isnull=False,
                            posting_key__isnull=True,
                            reason="",
                        )
                    )
                    | Q(
                        movement_type=CashMovementType.CASH_SALE,
                        amount_delta__gt=Decimal("0.00"),
                        source_id__isnull=False,
                        posting_key__isnull=True,
                        reason="",
                    )
                    | Q(
                        movement_type=CashMovementType.CASH_REFUND,
                        amount_delta__lt=Decimal("0.00"),
                        source_id__isnull=False,
                        posting_key__isnull=True,
                        reason="",
                    )
                    | Q(
                        movement_type=CashMovementType.CASH_ADDED,
                        amount_delta__gt=Decimal("0.00"),
                        source_id__isnull=True,
                        posting_key__isnull=False,
                    )
                    | Q(
                        movement_type=CashMovementType.CASH_REMOVED,
                        amount_delta__lt=Decimal("0.00"),
                        source_id__isnull=True,
                        posting_key__isnull=False,
                    )
                    | Q(
                        movement_type=CashMovementType.OPERATING_EXPENSE,
                        amount_delta__lt=Decimal("0.00"),
                        source_id__isnull=False,
                        posting_key__isnull=True,
                        reason="",
                    )
                    | Q(
                        movement_type=CashMovementType.OPERATING_EXPENSE_REVERSAL,
                        amount_delta__gt=Decimal("0.00"),
                        source_id__isnull=False,
                        posting_key__isnull=True,
                        reason="",
                    )
                    | Q(
                        movement_type=CashMovementType.SUPPLIER_PAYMENT,
                        amount_delta__lt=Decimal("0.00"),
                        source_id__isnull=False,
                        posting_key__isnull=True,
                        reason="",
                    )
                    | Q(
                        movement_type=CashMovementType.SUPPLIER_PAYMENT_REVERSAL,
                        amount_delta__gt=Decimal("0.00"),
                        source_id__isnull=False,
                        posting_key__isnull=True,
                        reason="",
                    )
                    | Q(
                        movement_type=CashMovementType.SUPPLIER_REFUND,
                        amount_delta__gt=Decimal("0.00"),
                        source_id__isnull=False,
                        posting_key__isnull=True,
                        reason="",
                    )
                    | Q(
                        movement_type=CashMovementType.SUPPLIER_REFUND_REVERSAL,
                        amount_delta__lt=Decimal("0.00"),
                        source_id__isnull=False,
                        posting_key__isnull=True,
                        reason="",
                    )
                ),
                name="cash_movement_evidence_matches_type",
            ),
            models.UniqueConstraint(
                fields=("business", "movement_type", "source_id"),
                condition=Q(source_id__isnull=False),
                name="cash_unique_movement_source_per_business",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_movement_type_display()} — {self.amount_delta}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Cash movements cannot be modified."))
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
        raise ValidationError(_("Cash movements cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.session_id:
            if self.session.business_id != self.business_id:
                errors["session"] = ValidationError(_("Cash session must belong to this business."))
            elif self.session.branch_id != self.branch_id:
                errors["session"] = ValidationError(_("Cash session must belong to this branch."))
        if self.actor_id and self.actor.business_id != self.business_id:
            errors["actor"] = ValidationError(_("Actor must belong to this business."))
        posting_key = self.posting_key
        if self.posting_key_id and posting_key is not None:
            if posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif (
                posting_key.operation_type != CashOperationType.MANUAL_MOVEMENT
                or posting_key.source_id != self.session_id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this manual cash movement.")
                )
        if (
            self.movement_type
            in {
                CashMovementType.CASH_ADDED,
                CashMovementType.CASH_REMOVED,
            }
            and not self.reason.strip()
        ):
            errors["reason"] = ValidationError(_("A manual cash movement reason is required."))
        if errors:
            raise ValidationError(errors)


class CashSessionClosure(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="cash_session_closures",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="cash_session_closures",
    )
    session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="closures",
    )
    sequence = models.PositiveIntegerField()
    expected_cash = models.DecimalField(max_digits=18, decimal_places=2)
    actual_cash = models.DecimalField(max_digits=18, decimal_places=2)
    variance = models.DecimalField(max_digits=18, decimal_places=2)
    explanation = models.TextField(blank=True)
    posting_key = models.OneToOneField(
        CashPostingKey,
        on_delete=models.PROTECT,
        related_name="cash_session_closure",
    )
    closed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="cash_sessions_closed",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("session", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("session", "sequence"),
                name="cash_unique_closure_sequence_per_session",
            ),
            models.CheckConstraint(
                condition=Q(expected_cash__gte=Decimal("0.00")),
                name="cash_closure_expected_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(actual_cash__gte=Decimal("0.00")),
                name="cash_closure_actual_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.session} — {self.sequence}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Cash session closures cannot be modified."))
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
        raise ValidationError(_("Cash session closures cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.session_id:
            if self.session.business_id != self.business_id:
                errors["session"] = ValidationError(_("Cash session must belong to this business."))
            elif self.session.branch_id != self.branch_id:
                errors["session"] = ValidationError(_("Cash session must belong to this branch."))
        if self.posting_key_id:
            if self.posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif (
                self.posting_key.operation_type != CashOperationType.CLOSE
                or self.posting_key.source_id != self.session_id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this cash-session closing.")
                )
        if self.closed_by_id and self.closed_by.business_id != self.business_id:
            errors["closed_by"] = ValidationError(_("Closing actor must belong to this business."))
        if self.variance != self.actual_cash - self.expected_cash:
            errors["variance"] = ValidationError(
                _("Variance must equal actual cash minus expected cash.")
            )
        if self.variance != 0 and not self.explanation.strip():
            errors["explanation"] = ValidationError(_("Explain every nonzero cash variance."))
        if errors:
            raise ValidationError(errors)


class CashSessionReopening(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="cash_session_reopenings",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="cash_session_reopenings",
    )
    session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="reopenings",
    )
    closure = models.OneToOneField(
        CashSessionClosure,
        on_delete=models.PROTECT,
        related_name="reopening",
    )
    reason = models.TextField()
    posting_key = models.OneToOneField(
        CashPostingKey,
        on_delete=models.PROTECT,
        related_name="cash_session_reopening",
    )
    reopened_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="cash_sessions_reopened",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("session", "posted_at")

    def __str__(self) -> str:
        return f"{self.session} — {self.posted_at}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Cash session reopenings cannot be modified."))
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
        raise ValidationError(_("Cash session reopenings cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.session_id:
            if self.session.business_id != self.business_id:
                errors["session"] = ValidationError(_("Cash session must belong to this business."))
            elif self.session.branch_id != self.branch_id:
                errors["session"] = ValidationError(_("Cash session must belong to this branch."))
        if self.closure_id and self.closure.session_id != self.session_id:
            errors["closure"] = ValidationError(
                _("Reopened closure must belong to this cash session.")
            )
        if self.posting_key_id:
            if self.posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif (
                self.posting_key.operation_type != CashOperationType.REOPEN
                or self.posting_key.source_id != self.session_id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this cash-session reopening.")
                )
        if self.reopened_by_id and self.reopened_by.business_id != self.business_id:
            errors["reopened_by"] = ValidationError(
                _("Reopening actor must belong to this business.")
            )
        if not self.reason.strip():
            errors["reason"] = ValidationError(_("A reopening reason is required."))
        if errors:
            raise ValidationError(errors)
