from django.contrib import admin
from django.http import HttpRequest

from apps.documents.models import (
    CapturedDocument,
    DocumentAccessEvent,
    DocumentFile,
    DocumentTranscription,
    DocumentTranscriptionLine,
    DocumentTranscriptionRevision,
)


class ImmutableDocumentAdmin(admin.ModelAdmin):
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


for model in (
    CapturedDocument,
    DocumentFile,
    DocumentTranscription,
    DocumentTranscriptionLine,
    DocumentTranscriptionRevision,
    DocumentAccessEvent,
):
    admin.site.register(model, ImmutableDocumentAdmin)
