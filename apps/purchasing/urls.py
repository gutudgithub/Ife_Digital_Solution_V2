from django.urls import path

from apps.purchasing import views

app_name = "purchasing"

urlpatterns = [
    path("", views.purchase_list, name="purchase-list"),
    path(
        "cost-history/",
        views.purchase_cost_history,
        name="purchase-cost-history",
    ),
    path("new/", views.purchase_create, name="purchase-create"),
    path("<uuid:purchase_id>/", views.purchase_detail, name="purchase-detail"),
    path("<uuid:purchase_id>/edit/", views.purchase_edit, name="purchase-edit"),
    path("<uuid:purchase_id>/approve/", views.purchase_approve, name="purchase-approve"),
    path("<uuid:purchase_id>/cancel/", views.purchase_cancel, name="purchase-cancel"),
    path("<uuid:purchase_id>/receive/", views.purchase_receive, name="purchase-receive"),
    path(
        "<uuid:purchase_id>/returns/new/",
        views.purchase_return_create,
        name="purchase-return-create",
    ),
    path("returns/", views.purchase_return_list, name="purchase-return-list"),
    path(
        "returns/<uuid:return_id>/",
        views.purchase_return_detail,
        name="purchase-return-detail",
    ),
    path(
        "returns/<uuid:return_id>/edit/",
        views.purchase_return_edit,
        name="purchase-return-edit",
    ),
    path(
        "returns/<uuid:return_id>/post/",
        views.purchase_return_post,
        name="purchase-return-post",
    ),
    path(
        "returns/<uuid:return_id>/cancel/",
        views.purchase_return_cancel,
        name="purchase-return-cancel",
    ),
    path(
        "returns/<uuid:return_id>/reverse/",
        views.purchase_return_reverse,
        name="purchase-return-reverse",
    ),
    path("suppliers/", views.supplier_list, name="supplier-list"),
    path("suppliers/new/", views.supplier_create, name="supplier-create"),
    path("suppliers/<uuid:supplier_id>/edit/", views.supplier_edit, name="supplier-edit"),
]
