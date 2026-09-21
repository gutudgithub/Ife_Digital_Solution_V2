from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.documents.provenance import (
    expense_confirmed_source_document,
    expense_has_confirmed_source,
)
from apps.expenses.forms import (
    ExpenseCategoryForm,
    ExpenseFilterForm,
    ExpensePostForm,
    OperatingExpenseForm,
    ReversalForm,
    SupplierPaymentForm,
    SupplierReturnSettlementForm,
)
from apps.expenses.models import (
    ExpenseCategory,
    ExpenseStatus,
    OperatingExpense,
    OperatingExpensePayment,
    OperatingExpenseReversal,
    SupplierPayment,
    SupplierPaymentReversal,
    SupplierReturnSettlement,
    SupplierReturnSettlementReversal,
)
from apps.expenses.services import (
    cancel_operating_expense_draft,
    create_operating_expense_draft,
    edit_operating_expense_draft,
    post_operating_expense,
    post_supplier_payment,
    post_supplier_return_settlement,
    purchase_return_settlement_totals,
    purchase_settlement_totals,
    reverse_operating_expense,
    reverse_supplier_payment,
    reverse_supplier_return_settlement,
)
from apps.forms import add_accessible_error_attributes
from apps.purchasing.models import Purchase, PurchaseReturn, PurchaseReturnStatus


def _tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    if tenant_request.active_business is None or membership is None:
        raise PermissionDenied(_("No active business membership is available."))
    if not membership.can_manage_operating_expenses:
        raise PermissionDenied(_("Owner or manager permission is required."))
    return tenant_request


def _expense(request: HttpRequest, expense_id: UUID) -> OperatingExpense:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    return get_object_or_404(
        OperatingExpense.objects.select_related(
            "branch",
            "category",
            "created_by__user",
            "posted_by__user",
            "cancelled_by__user",
        ),
        pk=expense_id,
        business=business,
    )


@login_required
def category_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    return render(
        request,
        "expenses/category_list.html",
        {"categories": ExpenseCategory.objects.filter(business=business)},
    )


def _category_form_response(
    request: HttpRequest,
    *,
    category: ExpenseCategory,
    business: Business,
) -> HttpResponse:
    form = ExpenseCategoryForm(
        request.POST or None,
        instance=category,
        business=business,
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.business = business
        try:
            saved.save()
        except ValidationError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Expense category saved."))
            return redirect("expenses:category-list")
    add_accessible_error_attributes(form)
    return render(
        request,
        "expenses/category_form.html",
        {"form": form, "category": category},
    )


@login_required
def category_create(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    return _category_form_response(
        request,
        category=ExpenseCategory(business=business),
        business=business,
    )


@login_required
def category_edit(request: HttpRequest, category_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    category = get_object_or_404(ExpenseCategory, pk=category_id, business=business)
    return _category_form_response(request, category=category, business=business)


@login_required
@require_POST
def category_toggle(request: HttpRequest, category_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    category = get_object_or_404(ExpenseCategory, pk=category_id, business=business)
    category.is_active = not category.is_active
    category.save(update_fields=("is_active", "updated_at"))
    messages.success(request, _("Expense category status updated."))
    return redirect("expenses:category-list")


@login_required
def expense_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    expenses = OperatingExpense.objects.filter(business=business).select_related(
        "branch",
        "category",
    )
    filter_form = ExpenseFilterForm(request.GET or None)
    filter_form.scope_to_business(business)
    if filter_form.is_valid():
        branch = filter_form.cleaned_data.get("branch")
        category = filter_form.cleaned_data.get("category")
        status = filter_form.cleaned_data.get("status")
        date_from = filter_form.cleaned_data.get("date_from")
        date_to = filter_form.cleaned_data.get("date_to")
        search = filter_form.cleaned_data.get("search")
        if branch is not None:
            expenses = expenses.filter(branch=branch)
        if category is not None:
            expenses = expenses.filter(category=category)
        if status:
            expenses = expenses.filter(status=status)
        if isinstance(date_from, date):
            expenses = expenses.filter(business_date__gte=date_from)
        if isinstance(date_to, date):
            expenses = expenses.filter(business_date__lte=date_to)
        if search:
            expenses = expenses.filter(
                Q(internal_number__icontains=search)
                | Q(payee__icontains=search)
                | Q(description__icontains=search)
            )
    page_obj = Paginator(expenses, 50).get_page(request.GET.get("page"))
    add_accessible_error_attributes(filter_form)
    return render(
        request,
        "expenses/expense_list.html",
        {
            "expenses": page_obj.object_list,
            "page_obj": page_obj,
            "filter_form": filter_form,
        },
    )


def _expense_form_response(
    request: HttpRequest,
    *,
    expense: OperatingExpense | None,
    business: Business,
    membership: BusinessMembership,
) -> HttpResponse:
    instance = expense or OperatingExpense(business=business, created_by=membership)
    form = OperatingExpenseForm(
        request.POST or None,
        instance=instance,
        business=business,
    )
    if request.method == "POST" and form.is_valid():
        try:
            if expense is None:
                saved = create_operating_expense_draft(
                    actor=membership,
                    branch=form.cleaned_data["branch"],
                    category=form.cleaned_data["category"],
                    payee=cast(str, form.cleaned_data["payee"]),
                    description=cast(str, form.cleaned_data["description"]),
                    amount=cast(Decimal, form.cleaned_data["amount"]),
                )
            else:
                saved = edit_operating_expense_draft(
                    actor=membership,
                    expense=expense,
                    branch=form.cleaned_data["branch"],
                    category=form.cleaned_data["category"],
                    payee=cast(str, form.cleaned_data["payee"]),
                    description=cast(str, form.cleaned_data["description"]),
                    amount=cast(Decimal, form.cleaned_data["amount"]),
                )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Operating expense draft saved."))
            return redirect("expenses:expense-detail", expense_id=saved.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "expenses/expense_form.html",
        {"form": form, "expense": expense},
    )


@login_required
def expense_create(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    return _expense_form_response(
        request,
        expense=None,
        business=cast(Business, tenant_request.active_business),
        membership=cast(BusinessMembership, tenant_request.active_membership),
    )


@login_required
def expense_edit(request: HttpRequest, expense_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    expense = _expense(request, expense_id)
    if expense.status != ExpenseStatus.DRAFT:
        raise PermissionDenied(_("Only draft operating expenses can be edited."))
    if expense_has_confirmed_source(expense):
        raise PermissionDenied(
            _(
                "This expense matches owner-confirmed document evidence. "
                "Cancel it and start a replacement transcription to correct it."
            )
        )
    return _expense_form_response(
        request,
        expense=expense,
        business=cast(Business, tenant_request.active_business),
        membership=cast(BusinessMembership, tenant_request.active_membership),
    )


@login_required
def expense_detail(request: HttpRequest, expense_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    expense = _expense(request, expense_id)
    payment = (
        OperatingExpensePayment.objects.filter(expense=expense)
        .select_related(
            "posted_by__user",
            "cash_session",
        )
        .first()
    )
    reversal = (
        OperatingExpenseReversal.objects.filter(expense=expense)
        .select_related("reversed_by__user")
        .first()
    )
    return render(
        request,
        "expenses/expense_detail.html",
        {
            "expense": expense,
            "payment": payment,
            "reversal": reversal,
            "source_document": (
                expense_confirmed_source_document(expense)
                if membership.can_manage_documents
                else None
            ),
        },
    )


@login_required
def expense_post(request: HttpRequest, expense_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    expense = _expense(request, expense_id)
    form = ExpensePostForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            post_operating_expense(
                actor=membership,
                expense=expense,
                method=cast(str, form.cleaned_data["method"]),
                telebirr_reference=cast(str, form.cleaned_data["telebirr_reference"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Operating expense posted."))
            return redirect("expenses:expense-detail", expense_id=expense.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "expenses/posting_form.html",
        {"form": form, "heading": _("Post operating expense"), "source": expense},
    )


@login_required
@require_POST
def expense_cancel(request: HttpRequest, expense_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    expense = _expense(request, expense_id)
    try:
        cancel_operating_expense_draft(
            actor=cast(BusinessMembership, tenant_request.active_membership),
            expense=expense,
        )
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, _("Operating expense draft cancelled."))
    return redirect("expenses:expense-detail", expense_id=expense.id)


@login_required
def expense_reverse(request: HttpRequest, expense_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    expense = _expense(request, expense_id)
    form = ReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reverse_operating_expense(
                actor=cast(BusinessMembership, tenant_request.active_membership),
                expense=expense,
                reason=cast(str, form.cleaned_data["reason"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Operating expense reversed."))
            return redirect("expenses:expense-detail", expense_id=expense.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "expenses/reversal_form.html",
        {"form": form, "heading": _("Reverse operating expense"), "source": expense},
    )


@login_required
def expense_print(request: HttpRequest, expense_id: UUID) -> HttpResponse:
    expense = _expense(request, expense_id)
    return render(
        request,
        "expenses/expense_print.html",
        {
            "expense": expense,
            "payment": OperatingExpensePayment.objects.filter(expense=expense).first(),
            "reversal": OperatingExpenseReversal.objects.filter(expense=expense).first(),
        },
    )


@login_required
def supplier_payment_create(request: HttpRequest, purchase_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase = get_object_or_404(
        Purchase.objects.select_related("supplier", "branch"),
        pk=purchase_id,
        business=business,
    )
    purchase_totals = purchase_settlement_totals(purchase)
    totals = (
        (
            _("Gross purchase reference"),
            purchase_totals["gross_purchase_reference"],
        ),
        (
            _("Accepted return reductions"),
            purchase_totals["accepted_return_reductions"],
        ),
        (
            _("Net purchase reference"),
            purchase_totals["net_purchase_reference"],
        ),
        (
            _("Net transferred to supplier"),
            purchase_totals["net_transferred_to_supplier"],
        ),
        (
            _("Remaining operational reference balance"),
            purchase_totals["remaining_operational_reference_balance"],
        ),
    )
    form = SupplierPaymentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            post_supplier_payment(
                actor=membership,
                purchase=purchase,
                amount=cast(Decimal, form.cleaned_data["amount"]),
                method=cast(str, form.cleaned_data["method"]),
                supplier_reference=cast(str, form.cleaned_data["supplier_reference"]),
                telebirr_reference=cast(str, form.cleaned_data["telebirr_reference"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Supplier payment posted."))
            return redirect("purchasing:purchase-detail", purchase_id=purchase.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "expenses/posting_form.html",
        {
            "form": form,
            "heading": _("Record supplier payment"),
            "source": purchase,
            "totals": totals,
        },
    )


@login_required
def supplier_payment_reverse(request: HttpRequest, payment_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    payment = get_object_or_404(
        SupplierPayment.objects.select_related("purchase"),
        pk=payment_id,
        business=business,
    )
    form = ReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reverse_supplier_payment(
                actor=cast(BusinessMembership, tenant_request.active_membership),
                supplier_payment=payment,
                reason=cast(str, form.cleaned_data["reason"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Supplier payment reversed."))
            return redirect("purchasing:purchase-detail", purchase_id=payment.purchase_id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "expenses/reversal_form.html",
        {"form": form, "heading": _("Reverse supplier payment"), "source": payment},
    )


@login_required
def return_settlement_create(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    purchase_return = get_object_or_404(
        PurchaseReturn.objects.select_related("purchase", "supplier", "branch"),
        pk=return_id,
        business=business,
        status=PurchaseReturnStatus.POSTED,
    )
    return_totals = purchase_return_settlement_totals(purchase_return)
    totals = (
        (
            _("Supplier return reference amount"),
            return_totals["supplier_return_reference_amount"],
        ),
        (
            _("Inventory-value reduction"),
            return_totals["inventory_value_reduction"],
        ),
        (
            _("Supplier-accepted settlement amount"),
            return_totals["supplier_accepted_settlement_amount"],
        ),
        (
            _("Unresolved supplier-return reference"),
            return_totals["unresolved_supplier_return_reference"],
        ),
    )
    form = SupplierReturnSettlementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            post_supplier_return_settlement(
                actor=cast(BusinessMembership, tenant_request.active_membership),
                purchase_return=purchase_return,
                settlement_type=cast(str, form.cleaned_data["settlement_type"]),
                amount=cast(Decimal, form.cleaned_data["amount"]),
                method=cast(str, form.cleaned_data["method"]),
                supplier_reference=cast(str, form.cleaned_data["supplier_reference"]),
                telebirr_reference=cast(str, form.cleaned_data["telebirr_reference"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Supplier return settlement posted."))
            return redirect(
                "purchasing:purchase-return-detail",
                return_id=purchase_return.id,
            )
    add_accessible_error_attributes(form)
    return render(
        request,
        "expenses/posting_form.html",
        {
            "form": form,
            "heading": _("Record supplier return settlement"),
            "source": purchase_return,
            "totals": totals,
        },
    )


@login_required
def return_settlement_reverse(request: HttpRequest, settlement_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    settlement = get_object_or_404(
        SupplierReturnSettlement.objects.select_related("purchase_return"),
        pk=settlement_id,
        business=business,
    )
    form = ReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reverse_supplier_return_settlement(
                actor=cast(BusinessMembership, tenant_request.active_membership),
                settlement=settlement,
                reason=cast(str, form.cleaned_data["reason"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Supplier return settlement reversed."))
            return redirect(
                "purchasing:purchase-return-detail",
                return_id=settlement.purchase_return_id,
            )
    add_accessible_error_attributes(form)
    return render(
        request,
        "expenses/reversal_form.html",
        {"form": form, "heading": _("Reverse return settlement"), "source": settlement},
    )


@login_required
def settlement_activity(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    payments = SupplierPayment.objects.filter(business=business).select_related(
        "purchase",
        "supplier",
        "branch",
    )
    settlements = SupplierReturnSettlement.objects.filter(business=business).select_related(
        "purchase",
        "purchase_return",
        "supplier",
        "branch",
    )
    return render(
        request,
        "expenses/settlement_activity.html",
        {"payments": payments[:50], "settlements": settlements[:50]},
    )


@login_required
def supplier_payment_print(request: HttpRequest, payment_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    payment = get_object_or_404(
        SupplierPayment.objects.select_related("purchase", "supplier", "branch", "posted_by__user"),
        pk=payment_id,
        business=business,
    )
    return render(
        request,
        "expenses/settlement_print.html",
        {
            "record": payment,
            "record_type": _("Supplier payment"),
            "reversal": SupplierPaymentReversal.objects.filter(supplier_payment=payment).first(),
        },
    )


@login_required
def return_settlement_print(request: HttpRequest, settlement_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    settlement = get_object_or_404(
        SupplierReturnSettlement.objects.select_related(
            "purchase",
            "purchase_return",
            "supplier",
            "branch",
            "posted_by__user",
        ),
        pk=settlement_id,
        business=business,
    )
    return render(
        request,
        "expenses/settlement_print.html",
        {
            "record": settlement,
            "record_type": settlement.get_settlement_type_display(),
            "reversal": SupplierReturnSettlementReversal.objects.filter(
                settlement=settlement
            ).first(),
        },
    )
