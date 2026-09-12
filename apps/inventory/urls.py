from django.urls import path

from apps.inventory import views

app_name = "inventory"

urlpatterns = [
    path("", views.inventory_list, name="inventory-list"),
    path("opening/", views.opening_balance_create, name="opening-create"),
    path("adjust/", views.inventory_adjust, name="inventory-adjust"),
    path("counts/", views.stock_count_list, name="stock-count-list"),
    path("counts/start/", views.stock_count_start, name="stock-count-start"),
    path(
        "counts/<uuid:session_id>/",
        views.stock_count_detail,
        name="stock-count-detail",
    ),
    path(
        "counts/<uuid:session_id>/worksheet/",
        views.stock_count_worksheet,
        name="stock-count-worksheet",
    ),
    path(
        "counts/<uuid:session_id>/submit/",
        views.stock_count_submit,
        name="stock-count-submit",
    ),
    path(
        "counts/<uuid:session_id>/review/",
        views.stock_count_review,
        name="stock-count-review",
    ),
    path(
        "counts/<uuid:session_id>/return/",
        views.stock_count_return,
        name="stock-count-return",
    ),
    path(
        "counts/<uuid:session_id>/cancel/",
        views.stock_count_cancel,
        name="stock-count-cancel",
    ),
    path(
        "counts/<uuid:session_id>/approve/",
        views.stock_count_approve,
        name="stock-count-approve",
    ),
    path(
        "counts/<uuid:session_id>/print/",
        views.stock_count_print,
        name="stock-count-print",
    ),
    path(
        "counts/<uuid:session_id>/reverse/",
        views.stock_count_reverse,
        name="stock-count-reverse",
    ),
]
