import uuid
from collections.abc import Iterable
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.cash.models import CashSession
from apps.purchasing.models import Purchase, PurchaseReturn, Supplier


class ExpenseStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    POSTED = "posted", _("Posted")
    REVERSED = "reversed", _("Reversed")
    CANCELLED = "cancelled", _("Cancelled")


class OperationalPaymentMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    TELEBIRR = "telebirr", _("Telebirr")


class SupplierReturnSettlementType(models.TextChoices):
    CREDIT = "credit", _("Credit accepted")
    REFUND = "refund", _("Refund recovered")


class ExpenseSettlementOperationType(models.TextChoices):
    EXPENSE = "expense", _("Operating expense")
    EXPENSE_REVERSAL = "expense_reversal", _("Operating expense reversal")
    SUPPLIER_PAYMENT = "supplier_payment", _("Supplier payment")
    SUPPLIER_PAYMENT_REVERSAL = (
        "supplier_payment_reversal",
        _("Supplier payment reversal"),
    )
    RETURN_SETTLEMENT = "return_settlement", _("Supplier return settlement")
    RETURN_SETTLEMENT_REVERSAL = (
        "return_settlement_reversal",
        _("Supplier return settlement reversal"),
    )


class ExpenseSettlementPostingKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="expense_settlement_posting_keys",
    )
    key = models.UUIDField()
    operation_type = models.CharField(
        max_length=32,
        choices=ExpenseSettlementOperationType.choices,
    )
    source_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "key"),
                name="expenses_unique_posting_key_business",
            ),
            models.CheckConstraint(
                condition=Q(operation_type__in=ExpenseSettlementOperationType.values),
                name="expenses_posting_operation_valid",
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
            raise ValidationError(_("Expense and settlement posting keys cannot be modified."))
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
        raise ValidationError(_("Expense and settlement posting keys cannot be deleted."))


class ExpenseCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="expense_categories",
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                models.F("business"),
                name="expenses_unique_category_name_business",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        self.name = self.name.strip()
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


class OperatingExpense(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="operating_expenses",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="operating_expenses",
    )
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    internal_number = models.CharField(max_length=40)
    business_date = models.DateField(null=True, blank=True)
    payee = models.CharField(max_length=180, blank=True)
    description = models.TextField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(
        max_length=12,
        choices=ExpenseStatus.choices,
        default=ExpenseStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="operating_expenses_created",
    )
    posting_key = models.OneToOneField(
        ExpenseSettlementPostingKey,
        on_delete=models.PROTECT,
        related_name="posted_expense",
        null=True,
        blank=True,
    )
    posted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="operating_expenses_posted",
        null=True,
        blank=True,
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="operating_expenses_cancelled",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-business_date", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "internal_number"),
                name="expenses_unique_number_business",
            ),
            models.CheckConstraint(
                condition=Q(status__in=ExpenseStatus.values),
                name="expenses_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="expenses_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=ExpenseStatus.DRAFT,
                        business_date__isnull=True,
                        posting_key__isnull=True,
                        posted_by__isnull=True,
                        posted_at__isnull=True,
                        cancelled_by__isnull=True,
                        cancelled_at__isnull=True,
                    )
                    | Q(
                        status__in=(ExpenseStatus.POSTED, ExpenseStatus.REVERSED),
                        business_date__isnull=False,
                        posting_key__isnull=False,
                        posted_by__isnull=False,
                        posted_at__isnull=False,
                        cancelled_by__isnull=True,
                        cancelled_at__isnull=True,
                    )
                    | Q(
                        status=ExpenseStatus.CANCELLED,
                        business_date__isnull=True,
                        posting_key__isnull=True,
                        posted_by__isnull=True,
                        posted_at__isnull=True,
                        cancelled_by__isnull=False,
                        cancelled_at__isnull=False,
                    )
                ),
                name="expenses_lifecycle_evidence_matches",
            ),
        ]

    def __str__(self) -> str:
        return self.internal_number

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            stored = OperatingExpense.objects.get(pk=self.pk)
            if stored.status != ExpenseStatus.DRAFT:
                if (
                    stored.business_id != self.business_id
                    or stored.branch_id != self.branch_id
                    or stored.category_id != self.category_id
                    or stored.internal_number != self.internal_number
                    or stored.business_date != self.business_date
                    or stored.payee != self.payee
                    or stored.description != self.description
                    or stored.amount != self.amount
                    or stored.created_by_id != self.created_by_id
                    or stored.posting_key_id != self.posting_key_id
                    or stored.posted_by_id != self.posted_by_id
                    or stored.posted_at != self.posted_at
                    or stored.cancelled_by_id != self.cancelled_by_id
                    or stored.cancelled_at != self.cancelled_at
                ):
                    raise ValidationError(_("Posted operating expenses cannot be modified."))
                if not (
                    stored.status == ExpenseStatus.POSTED and self.status == ExpenseStatus.REVERSED
                ):
                    raise ValidationError(_("Posted operating expenses cannot be modified."))
        self.payee = self.payee.strip()
        self.description = self.description.strip()
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
        stored_status = (
            OperatingExpense.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        )
        if stored_status != ExpenseStatus.DRAFT:
            raise ValidationError(_("Posted operating expenses cannot be deleted."))
        return super().delete(using=using, keep_parents=keep_parents)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.category_id and self.category.business_id != self.business_id:
            errors["category"] = ValidationError(_("Category must belong to this business."))
        if self.created_by_id and self.created_by.business_id != self.business_id:
            errors["created_by"] = ValidationError(_("Creator must belong to this business."))
        posted_by = self.posted_by
        if posted_by is not None and posted_by.business_id != self.business_id:
            errors["posted_by"] = ValidationError(_("Posting actor must belong to this business."))
        cancelled_by = self.cancelled_by
        if cancelled_by is not None and cancelled_by.business_id != self.business_id:
            errors["cancelled_by"] = ValidationError(
                _("Cancelling actor must belong to this business.")
            )
        posting_key = self.posting_key
        if posting_key is not None:
            if posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif (
                posting_key.operation_type != ExpenseSettlementOperationType.EXPENSE
                or posting_key.source_id != self.id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this operating expense.")
                )
        if not self.description:
            errors["description"] = ValidationError(_("An expense description is required."))
        if errors:
            raise ValidationError(errors)


class OperatingExpensePayment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="operating_expense_payments",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="operating_expense_payments",
    )
    expense = models.OneToOneField(
        OperatingExpense,
        on_delete=models.PROTECT,
        related_name="payment",
    )
    method = models.CharField(max_length=16, choices=OperationalPaymentMethod.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    telebirr_reference = models.CharField(max_length=120, blank=True)
    telebirr_reference_normalized = models.CharField(max_length=120, blank=True)
    cash_session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="operating_expense_payments",
        null=True,
        blank=True,
    )
    posted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="operating_expense_payments_posted",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(method__in=OperationalPaymentMethod.values),
                name="expenses_payment_method_valid",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="expenses_payment_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        method=OperationalPaymentMethod.CASH,
                        telebirr_reference="",
                        telebirr_reference_normalized="",
                        cash_session__isnull=False,
                    )
                    | (
                        Q(
                            method=OperationalPaymentMethod.TELEBIRR,
                            cash_session__isnull=True,
                        )
                        & ~Q(telebirr_reference="")
                        & ~Q(telebirr_reference_normalized="")
                    )
                ),
                name="expenses_payment_evidence_matches",
            ),
            models.UniqueConstraint(
                fields=("business", "telebirr_reference_normalized"),
                condition=Q(method=OperationalPaymentMethod.TELEBIRR),
                name="expenses_unique_payment_telebirr",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.expense} — {self.get_method_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Posted operating-expense payments cannot be modified."))
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
        raise ValidationError(_("Posted operating-expense payments cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.expense_id:
            if self.expense.business_id != self.business_id:
                errors["expense"] = ValidationError(
                    _("Operating expense must belong to this business.")
                )
            elif self.expense.branch_id != self.branch_id:
                errors["expense"] = ValidationError(
                    _("Operating expense must belong to this branch.")
                )
            if self.amount != self.expense.amount:
                errors["amount"] = ValidationError(
                    _("Expense payment must equal the complete expense amount.")
                )
        cash_session = self.cash_session
        if cash_session is not None:
            if cash_session.business_id != self.business_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this business.")
                )
            elif cash_session.branch_id != self.branch_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this branch.")
                )
        if self.posted_by_id and self.posted_by.business_id != self.business_id:
            errors["posted_by"] = ValidationError(_("Posting actor must belong to this business."))
        if errors:
            raise ValidationError(errors)


class OperatingExpenseReversal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="operating_expense_reversals",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="operating_expense_reversals",
    )
    expense = models.OneToOneField(
        OperatingExpense,
        on_delete=models.PROTECT,
        related_name="reversal",
    )
    posting_key = models.OneToOneField(
        ExpenseSettlementPostingKey,
        on_delete=models.PROTECT,
        related_name="expense_reversal",
    )
    reason = models.TextField()
    cash_session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="operating_expense_reversals",
        null=True,
        blank=True,
    )
    reversed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="operating_expenses_reversed",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)

    def __str__(self) -> str:
        return f"{self.expense} — {self.posted_at}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Operating-expense reversals cannot be modified."))
        self.reason = self.reason.strip()
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
        raise ValidationError(_("Operating-expense reversals cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.expense_id:
            if self.expense.business_id != self.business_id:
                errors["expense"] = ValidationError(
                    _("Operating expense must belong to this business.")
                )
            elif self.expense.branch_id != self.branch_id:
                errors["expense"] = ValidationError(
                    _("Operating expense must belong to this branch.")
                )
            payment_cash_session_id = (
                OperatingExpensePayment.objects.filter(expense_id=self.expense_id)
                .values_list("cash_session_id", flat=True)
                .first()
            )
            if payment_cash_session_id != self.cash_session_id:
                errors["cash_session"] = ValidationError(
                    _("Expense reversal must use the original payment cash session.")
                )
        if self.posting_key_id:
            if self.posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif self.expense_id and (
                self.posting_key.operation_type != ExpenseSettlementOperationType.EXPENSE_REVERSAL
                or self.posting_key.source_id != self.expense_id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this expense reversal.")
                )
        cash_session = self.cash_session
        if cash_session is not None:
            if cash_session.business_id != self.business_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this business.")
                )
            elif cash_session.branch_id != self.branch_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this branch.")
                )
        if self.reversed_by_id and self.reversed_by.business_id != self.business_id:
            errors["reversed_by"] = ValidationError(
                _("Reversing actor must belong to this business.")
            )
        if not self.reason:
            errors["reason"] = ValidationError(_("A reversal reason is required."))
        if errors:
            raise ValidationError(errors)


class SupplierPayment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="supplier_payments",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="supplier_payments",
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.PROTECT,
        related_name="supplier_payments",
    )
    business_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    method = models.CharField(max_length=16, choices=OperationalPaymentMethod.choices)
    supplier_reference = models.CharField(max_length=120, blank=True)
    telebirr_reference = models.CharField(max_length=120, blank=True)
    telebirr_reference_normalized = models.CharField(max_length=120, blank=True)
    cash_session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="supplier_payments",
        null=True,
        blank=True,
    )
    posting_key = models.OneToOneField(
        ExpenseSettlementPostingKey,
        on_delete=models.PROTECT,
        related_name="supplier_payment",
    )
    posted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="supplier_payments_posted",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(method__in=OperationalPaymentMethod.values),
                name="expenses_supplier_payment_method_valid",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="expenses_supplier_payment_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        method=OperationalPaymentMethod.CASH,
                        telebirr_reference="",
                        telebirr_reference_normalized="",
                        cash_session__isnull=False,
                    )
                    | (
                        Q(
                            method=OperationalPaymentMethod.TELEBIRR,
                            cash_session__isnull=True,
                        )
                        & ~Q(telebirr_reference="")
                        & ~Q(telebirr_reference_normalized="")
                    )
                ),
                name="expenses_supplier_payment_evidence",
            ),
            models.UniqueConstraint(
                fields=("business", "telebirr_reference_normalized"),
                condition=Q(method=OperationalPaymentMethod.TELEBIRR),
                name="expenses_unique_supplier_payment_ref",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.purchase} — {self.amount}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Supplier payments cannot be modified."))
        self.supplier_reference = self.supplier_reference.strip()
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
        raise ValidationError(_("Supplier payments cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.supplier_id and self.supplier.business_id != self.business_id:
            errors["supplier"] = ValidationError(_("Supplier must belong to this business."))
        if self.purchase_id:
            if self.purchase.business_id != self.business_id:
                errors["purchase"] = ValidationError(_("Purchase must belong to this business."))
            elif self.purchase.branch_id != self.branch_id:
                errors["purchase"] = ValidationError(_("Purchase must belong to this branch."))
            elif self.purchase.supplier_id != self.supplier_id:
                errors["purchase"] = ValidationError(_("Purchase must belong to this supplier."))
        cash_session = self.cash_session
        if cash_session is not None:
            if cash_session.business_id != self.business_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this business.")
                )
            elif cash_session.branch_id != self.branch_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this branch.")
                )
        if self.posting_key_id:
            if self.posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif (
                self.posting_key.operation_type != ExpenseSettlementOperationType.SUPPLIER_PAYMENT
                or self.posting_key.source_id != self.id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this supplier payment.")
                )
        if self.posted_by_id and self.posted_by.business_id != self.business_id:
            errors["posted_by"] = ValidationError(_("Posting actor must belong to this business."))
        if errors:
            raise ValidationError(errors)


class SupplierPaymentReversal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="supplier_payment_reversals",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="supplier_payment_reversals",
    )
    supplier_payment = models.OneToOneField(
        SupplierPayment,
        on_delete=models.PROTECT,
        related_name="reversal",
    )
    posting_key = models.OneToOneField(
        ExpenseSettlementPostingKey,
        on_delete=models.PROTECT,
        related_name="supplier_payment_reversal",
    )
    reason = models.TextField()
    cash_session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="supplier_payment_reversals",
        null=True,
        blank=True,
    )
    reversed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="supplier_payments_reversed",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)

    def __str__(self) -> str:
        return f"{self.supplier_payment} — {self.posted_at}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Supplier-payment reversals cannot be modified."))
        self.reason = self.reason.strip()
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
        raise ValidationError(_("Supplier-payment reversals cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.supplier_payment_id:
            payment = self.supplier_payment
            if payment.business_id != self.business_id:
                errors["supplier_payment"] = ValidationError(
                    _("Supplier payment must belong to this business.")
                )
            elif payment.branch_id != self.branch_id:
                errors["supplier_payment"] = ValidationError(
                    _("Supplier payment must belong to this branch.")
                )
            elif payment.cash_session_id != self.cash_session_id:
                errors["cash_session"] = ValidationError(
                    _("Supplier-payment reversal must use the original cash session.")
                )
        if self.posting_key_id:
            if self.posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif self.supplier_payment_id and (
                self.posting_key.operation_type
                != ExpenseSettlementOperationType.SUPPLIER_PAYMENT_REVERSAL
                or self.posting_key.source_id != self.supplier_payment_id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this supplier-payment reversal.")
                )
        cash_session = self.cash_session
        if cash_session is not None:
            if cash_session.business_id != self.business_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this business.")
                )
            elif cash_session.branch_id != self.branch_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this branch.")
                )
        if self.reversed_by_id and self.reversed_by.business_id != self.business_id:
            errors["reversed_by"] = ValidationError(
                _("Reversing actor must belong to this business.")
            )
        if not self.reason:
            errors["reason"] = ValidationError(_("A reversal reason is required."))
        if errors:
            raise ValidationError(errors)


class SupplierReturnSettlement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlements",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlements",
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="return_settlements",
    )
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.PROTECT,
        related_name="return_settlements",
    )
    purchase_return = models.ForeignKey(
        PurchaseReturn,
        on_delete=models.PROTECT,
        related_name="supplier_settlements",
    )
    business_date = models.DateField()
    settlement_type = models.CharField(
        max_length=12,
        choices=SupplierReturnSettlementType.choices,
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    method = models.CharField(
        max_length=16,
        choices=OperationalPaymentMethod.choices,
        blank=True,
    )
    supplier_reference = models.CharField(max_length=120, blank=True)
    telebirr_reference = models.CharField(max_length=120, blank=True)
    telebirr_reference_normalized = models.CharField(max_length=120, blank=True)
    cash_session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlements",
        null=True,
        blank=True,
    )
    posting_key = models.OneToOneField(
        ExpenseSettlementPostingKey,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlement",
    )
    posted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlements_posted",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(settlement_type__in=SupplierReturnSettlementType.values),
                name="expenses_return_settlement_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="expenses_return_settlement_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        settlement_type=SupplierReturnSettlementType.CREDIT,
                        method="",
                        telebirr_reference="",
                        telebirr_reference_normalized="",
                        cash_session__isnull=True,
                    )
                    | Q(
                        settlement_type=SupplierReturnSettlementType.REFUND,
                        method=OperationalPaymentMethod.CASH,
                        telebirr_reference="",
                        telebirr_reference_normalized="",
                        cash_session__isnull=False,
                    )
                    | (
                        Q(
                            settlement_type=SupplierReturnSettlementType.REFUND,
                            method=OperationalPaymentMethod.TELEBIRR,
                            cash_session__isnull=True,
                        )
                        & ~Q(telebirr_reference="")
                        & ~Q(telebirr_reference_normalized="")
                    )
                ),
                name="expenses_return_settlement_evidence",
            ),
            models.UniqueConstraint(
                fields=("business", "telebirr_reference_normalized"),
                condition=Q(
                    settlement_type=SupplierReturnSettlementType.REFUND,
                    method=OperationalPaymentMethod.TELEBIRR,
                ),
                name="expenses_unique_supplier_refund_ref",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.purchase_return} — {self.get_settlement_type_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Supplier-return settlements cannot be modified."))
        self.supplier_reference = self.supplier_reference.strip()
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
        raise ValidationError(_("Supplier-return settlements cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.supplier_id and self.supplier.business_id != self.business_id:
            errors["supplier"] = ValidationError(_("Supplier must belong to this business."))
        if self.purchase_id:
            if self.purchase.business_id != self.business_id:
                errors["purchase"] = ValidationError(_("Purchase must belong to this business."))
            elif self.purchase.branch_id != self.branch_id:
                errors["purchase"] = ValidationError(_("Purchase must belong to this branch."))
            elif self.purchase.supplier_id != self.supplier_id:
                errors["purchase"] = ValidationError(_("Purchase must belong to this supplier."))
        if self.purchase_return_id:
            if self.purchase_return.business_id != self.business_id:
                errors["purchase_return"] = ValidationError(
                    _("Purchase return must belong to this business.")
                )
            elif self.purchase_return.branch_id != self.branch_id:
                errors["purchase_return"] = ValidationError(
                    _("Purchase return must belong to this branch.")
                )
            elif self.purchase_return.purchase_id != self.purchase_id:
                errors["purchase_return"] = ValidationError(
                    _("Purchase return must belong to this purchase.")
                )
            elif self.purchase_return.supplier_id != self.supplier_id:
                errors["purchase_return"] = ValidationError(
                    _("Purchase return must belong to this supplier.")
                )
        cash_session = self.cash_session
        if cash_session is not None:
            if cash_session.business_id != self.business_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this business.")
                )
            elif cash_session.branch_id != self.branch_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this branch.")
                )
        if self.posting_key_id:
            if self.posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif (
                self.posting_key.operation_type != ExpenseSettlementOperationType.RETURN_SETTLEMENT
                or self.posting_key.source_id != self.id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this supplier-return settlement.")
                )
        if self.posted_by_id and self.posted_by.business_id != self.business_id:
            errors["posted_by"] = ValidationError(_("Posting actor must belong to this business."))
        if errors:
            raise ValidationError(errors)


class SupplierReturnSettlementReversal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlement_reversals",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlement_reversals",
    )
    settlement = models.OneToOneField(
        SupplierReturnSettlement,
        on_delete=models.PROTECT,
        related_name="reversal",
    )
    posting_key = models.OneToOneField(
        ExpenseSettlementPostingKey,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlement_reversal",
    )
    reason = models.TextField()
    cash_session = models.ForeignKey(
        CashSession,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlement_reversals",
        null=True,
        blank=True,
    )
    reversed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="supplier_return_settlements_reversed",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)

    def __str__(self) -> str:
        return f"{self.settlement} — {self.posted_at}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Supplier-return settlement reversals cannot be modified."))
        self.reason = self.reason.strip()
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
        raise ValidationError(_("Supplier-return settlement reversals cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.settlement_id:
            if self.settlement.business_id != self.business_id:
                errors["settlement"] = ValidationError(
                    _("Supplier-return settlement must belong to this business.")
                )
            elif self.settlement.branch_id != self.branch_id:
                errors["settlement"] = ValidationError(
                    _("Supplier-return settlement must belong to this branch.")
                )
            elif self.settlement.cash_session_id != self.cash_session_id:
                errors["cash_session"] = ValidationError(
                    _("Settlement reversal must use the original cash session.")
                )
        if self.posting_key_id:
            if self.posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif self.settlement_id and (
                self.posting_key.operation_type
                != ExpenseSettlementOperationType.RETURN_SETTLEMENT_REVERSAL
                or self.posting_key.source_id != self.settlement_id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this settlement reversal.")
                )
        cash_session = self.cash_session
        if cash_session is not None:
            if cash_session.business_id != self.business_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this business.")
                )
            elif cash_session.branch_id != self.branch_id:
                errors["cash_session"] = ValidationError(
                    _("Cash session must belong to this branch.")
                )
        if self.reversed_by_id and self.reversed_by.business_id != self.business_id:
            errors["reversed_by"] = ValidationError(
                _("Reversing actor must belong to this business.")
            )
        if not self.reason:
            errors["reason"] = ValidationError(_("A reversal reason is required."))
        if errors:
            raise ValidationError(errors)
