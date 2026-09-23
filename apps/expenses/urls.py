from django.urls import path

from apps.expenses import views

app_name = "expenses"

urlpatterns = [
    path("", views.expense_list, name="expense-list"),
    path("new/", views.expense_create, name="expense-create"),
    path("<uuid:expense_id>/", views.expense_detail, name="expense-detail"),
    path("<uuid:expense_id>/edit/", views.expense_edit, name="expense-edit"),
    path("<uuid:expense_id>/post/", views.expense_post, name="expense-post"),
    path("<uuid:expense_id>/cancel/", views.expense_cancel, name="expense-cancel"),
    path("<uuid:expense_id>/reverse/", views.expense_reverse, name="expense-reverse"),
    path("<uuid:expense_id>/print/", views.expense_print, name="expense-print"),
    path("categories/", views.category_list, name="category-list"),
    path("categories/new/", views.category_create, name="category-create"),
    path(
        "categories/<uuid:category_id>/edit/",
        views.category_edit,
        name="category-edit",
    ),
    path(
        "categories/<uuid:category_id>/toggle/",
        views.category_toggle,
        name="category-toggle",
    ),
    path(
        "purchases/<uuid:purchase_id>/payments/new/",
        views.supplier_payment_create,
        name="supplier-payment-create",
    ),
    path(
        "supplier-payments/<uuid:payment_id>/reverse/",
        views.supplier_payment_reverse,
        name="supplier-payment-reverse",
    ),
    path(
        "supplier-payments/<uuid:payment_id>/print/",
        views.supplier_payment_print,
        name="supplier-payment-print",
    ),
    path(
        "purchase-returns/<uuid:return_id>/settlements/new/",
        views.return_settlement_create,
        name="return-settlement-create",
    ),
    path(
        "return-settlements/<uuid:settlement_id>/reverse/",
        views.return_settlement_reverse,
        name="return-settlement-reverse",
    ),
    path(
        "return-settlements/<uuid:settlement_id>/print/",
        views.return_settlement_print,
        name="return-settlement-print",
    ),
    path("supplier-activity/", views.settlement_activity, name="settlement-activity"),
]
