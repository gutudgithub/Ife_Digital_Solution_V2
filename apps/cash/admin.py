from django.contrib import admin
from django.http import HttpRequest

from apps.cash.models import (
    CashMovement,
    CashPostingKey,
    CashSession,
    CashSessionClosure,
    CashSessionReopening,
)


class ImmutableCashAdmin(admin.ModelAdmin):
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False


@admin.register(CashSession)
class CashSessionAdmin(ImmutableCashAdmin):
    list_display = (
        "business_date",
        "business",
        "branch",
        "status",
        "opening_float",
        "opened_by",
        "opened_at",
    )
    list_filter = ("business", "branch", "status", "business_date")
    readonly_fields = (
        "business",
        "branch",
        "business_date",
        "status",
        "opening_float",
        "opening_key",
        "opened_by",
        "opened_at",
        "created_at",
        "updated_at",
    )


@admin.register(CashMovement)
class CashMovementAdmin(ImmutableCashAdmin):
    list_display = (
        "posted_at",
        "business",
        "branch",
        "session",
        "movement_type",
        "amount_delta",
    )
    list_filter = ("business", "branch", "movement_type", "posted_at")
    search_fields = ("reason", "actor__user__email")
    readonly_fields = (
        "business",
        "branch",
        "session",
        "movement_type",
        "amount_delta",
        "source_id",
        "posting_key",
        "actor",
        "reason",
        "posted_at",
    )


@admin.register(CashSessionClosure)
class CashSessionClosureAdmin(ImmutableCashAdmin):
    list_display = (
        "session",
        "sequence",
        "expected_cash",
        "actual_cash",
        "variance",
        "closed_by",
        "posted_at",
    )
    list_filter = ("business", "branch", "posted_at")
    readonly_fields = (
        "business",
        "branch",
        "session",
        "sequence",
        "expected_cash",
        "actual_cash",
        "variance",
        "explanation",
        "posting_key",
        "closed_by",
        "posted_at",
    )


@admin.register(CashSessionReopening)
class CashSessionReopeningAdmin(ImmutableCashAdmin):
    list_display = ("session", "closure", "reopened_by", "posted_at")
    list_filter = ("business", "branch", "posted_at")
    readonly_fields = (
        "business",
        "branch",
        "session",
        "closure",
        "reason",
        "posting_key",
        "reopened_by",
        "posted_at",
    )


@admin.register(CashPostingKey)
class CashPostingKeyAdmin(ImmutableCashAdmin):
    list_display = ("key", "business", "operation_type", "source_id", "created_at")
    list_filter = ("business", "operation_type", "created_at")
    readonly_fields = (
        "business",
        "key",
        "operation_type",
        "source_id",
        "created_at",
    )
