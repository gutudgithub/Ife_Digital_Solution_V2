from django.contrib import admin
from django.http import HttpRequest

from apps.expenses.models import (
    ExpenseCategory,
    ExpenseSettlementPostingKey,
    OperatingExpense,
    OperatingExpensePayment,
    OperatingExpenseReversal,
    SupplierPayment,
    SupplierPaymentReversal,
    SupplierReturnSettlement,
    SupplierReturnSettlementReversal,
)


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "business__name")


class ImmutableOperationalAdmin(admin.ModelAdmin):
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: object | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: object | None = None,
    ) -> bool:
        return False


for evidence_model in (
    ExpenseSettlementPostingKey,
    OperatingExpense,
    OperatingExpensePayment,
    OperatingExpenseReversal,
    SupplierPayment,
    SupplierPaymentReversal,
    SupplierReturnSettlement,
    SupplierReturnSettlementReversal,
):
    admin.site.register(evidence_model, ImmutableOperationalAdmin)
