from django.contrib import admin
from django.http import HttpRequest

from apps.offline.models import OfflineSaleSync, OfflineSaleSyncKey


class HiddenImmutableOfflineAdmin(admin.ModelAdmin):
    def has_module_permission(self, request: HttpRequest) -> bool:
        return False

    def has_view_permission(
        self,
        request: HttpRequest,
        obj: object | None = None,
    ) -> bool:
        return False

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


admin.site.register(OfflineSaleSyncKey, HiddenImmutableOfflineAdmin)
admin.site.register(OfflineSaleSync, HiddenImmutableOfflineAdmin)
