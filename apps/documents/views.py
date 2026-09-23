from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.documents.forms import (
    BaseTranscriptionLineFormSet,
    ConfirmationForm,
    DocumentCaptureForm,
    DocumentFilterForm,
    ExpenseTranscriptionForm,
    OpeningStockTranscriptionForm,
    PurchaseTranscriptionForm,
    ReasonForm,
    TranscriptionLineFormSet,
    transcription_line_initial,
)
from apps.documents.models import (
    CapturedDocument,
    DocumentAccessAction,
    DocumentFile,
    DocumentKind,
    DocumentStatus,
    DocumentTranscription,
    TranscriptionStatus,
)
from apps.documents.services import (
    ExpenseTranscriptionInput,
    OpeningStockTranscriptionInput,
    PurchaseTranscriptionInput,
    cancel_document,
    cancel_transcription,
    capture_document,
    confirm_transcription,
    post_confirmed_opening_stock,
    record_document_access,
    return_transcription_to_draft,
    save_expense_transcription,
    save_opening_stock_transcription,
    save_purchase_transcription,
    scan_document_files,
    start_replacement_transcription,
    submit_transcription_for_confirmation,
)
from apps.expenses.models import ExpenseCategory
from apps.forms import add_accessible_error_attributes
from apps.purchasing.models import Supplier


def _tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    if (
        tenant_request.active_business is None
        or membership is None
        or not membership.can_manage_documents
    ):
        raise PermissionDenied(_("Owner or manager document permission is required."))
    return tenant_request


def _document(request: HttpRequest, document_id: UUID) -> CapturedDocument:
    tenant_request = _tenant(request)
    return get_object_or_404(
        CapturedDocument.objects.select_related(
            "branch",
            "uploaded_by__user",
            "cancelled_by__user",
        ).prefetch_related(
            "files",
            "transcriptions__created_by__user",
            "transcriptions__submitted_by__user",
            "transcriptions__confirmed_by__user",
            "transcriptions__cancelled_by__user",
        ),
        pk=document_id,
        business=tenant_request.active_business,
    )


def _transcription(
    request: HttpRequest,
    transcription_id: UUID,
) -> DocumentTranscription:
    tenant_request = _tenant(request)
    return get_object_or_404(
        DocumentTranscription.objects.select_related(
            "document",
            "branch",
            "supplier",
            "expense_category",
            "target_purchase",
            "target_expense",
            "created_by__user",
            "submitted_by__user",
            "confirmed_by__user",
            "cancelled_by__user",
            "replacement_of",
        ).prefetch_related(
            "lines__variant__product",
            "lines__target_purchase_line",
            "lines__target_stock_operation",
            "revisions__actor__user",
        ),
        pk=transcription_id,
        business=tenant_request.active_business,
    )


def _private_response[ResponseT: HttpResponseBase](response: ResponseT) -> ResponseT:
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_safe
def document_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    documents = CapturedDocument.objects.filter(
        business=tenant_request.active_business,
    ).select_related("branch", "uploaded_by__user")
    filter_form = DocumentFilterForm(request.GET)
    if filter_form.is_valid():
        kind = filter_form.cleaned_data["kind"]
        status = filter_form.cleaned_data["status"]
        if kind:
            documents = documents.filter(kind=kind)
        if status:
            documents = documents.filter(status=status)
    page_obj = Paginator(documents, 25).get_page(request.GET.get("page"))
    return _private_response(
        render(
            request,
            "documents/document_list.html",
            {
                "documents": page_obj.object_list,
                "page_obj": page_obj,
                "filter_form": filter_form,
            },
        )
    )


@login_required
@require_http_methods(["GET", "POST"])
def document_capture(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    form = DocumentCaptureForm(
        request.POST or None,
        request.FILES or None,
        business=business,
    )
    if request.method == "POST" and form.is_valid():
        try:
            document, duplicate = capture_document(
                actor=membership,
                branch=form.cleaned_data["branch"],
                kind=cast(str, form.cleaned_data["kind"]),
                title=cast(str, form.cleaned_data["title"]),
                uploads=cast(list, form.cleaned_data["source_files"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            if duplicate:
                messages.info(request, _("An identical captured document already exists."))
            elif document.status == DocumentStatus.AVAILABLE:
                messages.success(request, _("Document uploaded and passed the malware scan."))
            else:
                messages.warning(
                    request,
                    _("Document is quarantined until every source file passes scanning."),
                )
            return redirect("documents:detail", document_id=document.id)
    add_accessible_error_attributes(form)
    return _private_response(render(request, "documents/document_capture.html", {"form": form}))


@login_required
@require_safe
def document_detail(request: HttpRequest, document_id: UUID) -> HttpResponse:
    document = _document(request, document_id)
    transcriptions = list(document.transcriptions.all())
    return _private_response(
        render(
            request,
            "documents/document_detail.html",
            {
                "document": document,
                "files": document.files.all(),
                "transcriptions": transcriptions,
                "membership": cast(TenantRequest, request).active_membership,
                "can_cancel_document": (
                    document.status != DocumentStatus.CANCELLED
                    and not any(
                        item.status == TranscriptionStatus.CONFIRMED for item in transcriptions
                    )
                ),
            },
        )
    )


@login_required
@require_safe
def document_file_preview(
    request: HttpRequest,
    document_id: UUID,
    file_id: UUID,
) -> HttpResponseBase:
    document = _document(request, document_id)
    document_file = get_object_or_404(
        DocumentFile,
        pk=file_id,
        document=document,
        business=document.business,
    )
    if document_file.media_type == "application/pdf":
        return document_file_download(request, document_id, file_id)
    membership = cast(BusinessMembership, cast(TenantRequest, request).active_membership)
    record_document_access(
        actor=membership,
        document_file=document_file,
        action=DocumentAccessAction.PREVIEW,
    )
    response = FileResponse(
        document_file.source.open("rb"),
        content_type=document_file.media_type,
    )
    return _private_response(response)


@login_required
@require_safe
def document_file_download(
    request: HttpRequest,
    document_id: UUID,
    file_id: UUID,
) -> HttpResponseBase:
    document = _document(request, document_id)
    document_file = get_object_or_404(
        DocumentFile,
        pk=file_id,
        document=document,
        business=document.business,
    )
    membership = cast(BusinessMembership, cast(TenantRequest, request).active_membership)
    record_document_access(
        actor=membership,
        document_file=document_file,
        action=DocumentAccessAction.DOWNLOAD,
    )
    response = FileResponse(
        document_file.source.open("rb"),
        as_attachment=True,
        filename=document_file.original_name,
        content_type=document_file.media_type,
    )
    return _private_response(response)


def _line_formset(
    request: HttpRequest,
    *,
    business: Business,
    transcription: DocumentTranscription,
) -> BaseTranscriptionLineFormSet:
    formset = cast(
        BaseTranscriptionLineFormSet,
        TranscriptionLineFormSet(
            request.POST or None,
            initial=transcription_line_initial(transcription),
            prefix="lines",
        ),
    )
    formset.scope_to_business(business)
    return formset


@login_required
@require_http_methods(["GET", "POST"])
def transcription_edit(request: HttpRequest, transcription_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    transcription = _transcription(request, transcription_id)
    if transcription.status != TranscriptionStatus.DRAFT:
        raise PermissionDenied(_("Only a draft transcription can be edited."))
    line_formset: BaseTranscriptionLineFormSet | None = None
    form: forms.BaseForm
    if transcription.document.kind == DocumentKind.PURCHASE:
        form = PurchaseTranscriptionForm(
            request.POST or None,
            business=business,
            transcription=transcription,
        )
        line_formset = _line_formset(
            request,
            business=business,
            transcription=transcription,
        )
    elif transcription.document.kind == DocumentKind.EXPENSE:
        form = ExpenseTranscriptionForm(
            request.POST or None,
            business=business,
            transcription=transcription,
        )
    else:
        form = OpeningStockTranscriptionForm(
            request.POST or None,
            business=business,
            transcription=transcription,
        )
        line_formset = _line_formset(
            request,
            business=business,
            transcription=transcription,
        )
    if request.method == "POST":
        form_valid = form.is_valid()
        formset_valid = line_formset is None or line_formset.is_valid()
        if form_valid and formset_valid:
            try:
                if transcription.document.kind == DocumentKind.PURCHASE:
                    if line_formset is None:
                        raise TypeError("Purchase transcription requires line forms.")
                    save_purchase_transcription(
                        actor=membership,
                        transcription=transcription,
                        data=PurchaseTranscriptionInput(
                            branch=form.cleaned_data["branch"],
                            supplier=cast(Supplier, form.cleaned_data["supplier"]),
                            purchase_date=cast(date, form.cleaned_data["purchase_date"]),
                            supplier_reference=cast(
                                str,
                                form.cleaned_data["supplier_reference"],
                            ),
                            expected_date=cast(
                                date | None,
                                form.cleaned_data["expected_date"],
                            ),
                            settlement_terms=cast(
                                str,
                                form.cleaned_data["settlement_terms"],
                            ),
                            lines=line_formset.line_inputs(),
                        ),
                    )
                elif transcription.document.kind == DocumentKind.EXPENSE:
                    save_expense_transcription(
                        actor=membership,
                        transcription=transcription,
                        data=ExpenseTranscriptionInput(
                            branch=form.cleaned_data["branch"],
                            category=cast(
                                ExpenseCategory,
                                form.cleaned_data["expense_category"],
                            ),
                            document_date=cast(date, form.cleaned_data["document_date"]),
                            payee=cast(str, form.cleaned_data["payee"]),
                            description=cast(str, form.cleaned_data["description"]),
                            amount=cast(Decimal, form.cleaned_data["amount"]),
                            payment_method=cast(
                                str,
                                form.cleaned_data["payment_method"],
                            ),
                            telebirr_reference=cast(
                                str,
                                form.cleaned_data["telebirr_reference"],
                            ),
                        ),
                    )
                else:
                    if line_formset is None:
                        raise TypeError("Opening-stock transcription requires line forms.")
                    save_opening_stock_transcription(
                        actor=membership,
                        transcription=transcription,
                        data=OpeningStockTranscriptionInput(
                            branch=form.cleaned_data["branch"],
                            lines=line_formset.line_inputs(),
                        ),
                    )
            except (PermissionDenied, ValidationError) as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, _("Transcription draft saved."))
                return redirect(
                    "documents:detail",
                    document_id=transcription.document_id,
                )
    add_accessible_error_attributes(form)
    if line_formset is not None:
        for line_form in line_formset.forms:
            add_accessible_error_attributes(line_form)
    return _private_response(
        render(
            request,
            "documents/transcription_form.html",
            {
                "form": form,
                "line_formset": line_formset,
                "transcription": transcription,
                "files": transcription.document.files.all(),
            },
        )
    )


@login_required
@require_POST
def transcription_submit(request: HttpRequest, transcription_id: UUID) -> HttpResponse:
    transcription = _transcription(request, transcription_id)
    try:
        submit_transcription_for_confirmation(
            actor=cast(
                BusinessMembership,
                cast(TenantRequest, request).active_membership,
            ),
            transcription=transcription,
        )
    except (PermissionDenied, ValidationError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("Transcription submitted for owner confirmation."))
    return redirect("documents:detail", document_id=transcription.document_id)


@login_required
@require_http_methods(["GET", "POST"])
def transcription_confirm(request: HttpRequest, transcription_id: UUID) -> HttpResponse:
    transcription = _transcription(request, transcription_id)
    membership = cast(BusinessMembership, cast(TenantRequest, request).active_membership)
    if not membership.can_confirm_documents:
        raise PermissionDenied(_("Only an active owner can confirm document transcriptions."))
    form = ConfirmationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            confirm_transcription(
                actor=membership,
                transcription=transcription,
                confirmation_key=cast(UUID, form.cleaned_data["confirmation_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(
                request,
                _("Transcription confirmed. Any operational posting remains a separate action."),
            )
            return redirect("documents:detail", document_id=transcription.document_id)
    add_accessible_error_attributes(form)
    return _private_response(
        render(
            request,
            "documents/transcription_confirm.html",
            {
                "form": form,
                "transcription": transcription,
                "files": transcription.document.files.all(),
            },
        )
    )


@login_required
@require_http_methods(["GET", "POST"])
def transcription_return(request: HttpRequest, transcription_id: UUID) -> HttpResponse:
    transcription = _transcription(request, transcription_id)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            return_transcription_to_draft(
                actor=cast(
                    BusinessMembership,
                    cast(TenantRequest, request).active_membership,
                ),
                transcription=transcription,
                reason=cast(str, form.cleaned_data["reason"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Transcription returned for correction."))
            return redirect("documents:detail", document_id=transcription.document_id)
    add_accessible_error_attributes(form)
    return _private_response(
        render(
            request,
            "documents/reason_form.html",
            {
                "form": form,
                "heading": _("Return transcription for correction"),
                "transcription": transcription,
            },
        )
    )


@login_required
@require_http_methods(["GET", "POST"])
def transcription_cancel(request: HttpRequest, transcription_id: UUID) -> HttpResponse:
    transcription = _transcription(request, transcription_id)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cancel_transcription(
                actor=cast(
                    BusinessMembership,
                    cast(TenantRequest, request).active_membership,
                ),
                transcription=transcription,
                reason=cast(str, form.cleaned_data["reason"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Unconfirmed transcription cancelled."))
            return redirect("documents:detail", document_id=transcription.document_id)
    add_accessible_error_attributes(form)
    return _private_response(
        render(
            request,
            "documents/cancel_form.html",
            {
                "form": form,
                "heading": _("Cancel transcription attempt"),
                "transcription": transcription,
            },
        )
    )


@login_required
@require_POST
def transcription_replace(request: HttpRequest, transcription_id: UUID) -> HttpResponse:
    transcription = _transcription(request, transcription_id)
    try:
        replacement = start_replacement_transcription(
            actor=cast(
                BusinessMembership,
                cast(TenantRequest, request).active_membership,
            ),
            transcription=transcription,
        )
    except (PermissionDenied, ValidationError) as error:
        messages.error(request, str(error))
        return redirect("documents:detail", document_id=transcription.document_id)
    messages.success(request, _("Replacement transcription started."))
    return redirect("documents:transcription-edit", transcription_id=replacement.id)


@login_required
@require_POST
def transcription_post_opening_stock(
    request: HttpRequest,
    transcription_id: UUID,
) -> HttpResponse:
    transcription = _transcription(request, transcription_id)
    try:
        post_confirmed_opening_stock(
            actor=cast(
                BusinessMembership,
                cast(TenantRequest, request).active_membership,
            ),
            transcription=transcription,
        )
    except (PermissionDenied, ValidationError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("Confirmed opening-stock batch posted atomically."))
    return redirect("documents:detail", document_id=transcription.document_id)


@login_required
@require_POST
def document_retry_scan(request: HttpRequest, document_id: UUID) -> HttpResponse:
    document = _document(request, document_id)
    try:
        scan_document_files(
            actor=cast(
                BusinessMembership,
                cast(TenantRequest, request).active_membership,
            ),
            document=document,
        )
    except (PermissionDenied, ValidationError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("Document scan retried."))
    return redirect("documents:detail", document_id=document.id)


@login_required
@require_http_methods(["GET", "POST"])
def document_cancel(request: HttpRequest, document_id: UUID) -> HttpResponse:
    document = _document(request, document_id)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cancel_document(
                actor=cast(
                    BusinessMembership,
                    cast(TenantRequest, request).active_membership,
                ),
                document=document,
                reason=cast(str, form.cleaned_data["reason"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Captured document cancelled."))
            return redirect("documents:detail", document_id=document.id)
    add_accessible_error_attributes(form)
    return _private_response(
        render(
            request,
            "documents/cancel_form.html",
            {
                "form": form,
                "heading": _("Cancel captured document"),
                "document": document,
            },
        )
    )
