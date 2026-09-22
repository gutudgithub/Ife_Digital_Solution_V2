from django.urls import path

from apps.sales import views

app_name = "sales"

urlpatterns = [
    path("", views.sale_list, name="sale-list"),
    path("telebirr/", views.telebirr_settings, name="telebirr-settings"),
    path(
        "telebirr/<uuid:profile_id>/activate/",
        views.telebirr_activate,
        name="telebirr-activate",
    ),
    path(
        "telebirr/<uuid:profile_id>/deactivate/",
        views.telebirr_deactivate,
        name="telebirr-deactivate",
    ),
    path(
        "telebirr/<uuid:profile_id>/remove/",
        views.telebirr_remove,
        name="telebirr-remove",
    ),
    path(
        "telebirr/<uuid:profile_id>/qr/",
        views.telebirr_qr,
        name="telebirr-qr",
    ),
    path("new/", views.sale_create, name="sale-create"),
    path("<uuid:sale_id>/", views.sale_detail, name="sale-detail"),
    path("<uuid:sale_id>/edit/", views.sale_edit, name="sale-edit"),
    path("<uuid:sale_id>/post/", views.sale_post, name="sale-post"),
    path("<uuid:sale_id>/cancel/", views.sale_cancel, name="sale-cancel"),
    path(
        "<uuid:sale_id>/returns/new/<str:purpose>/",
        views.sale_return_create,
        name="return-create",
    ),
    path("returns/", views.sale_return_list, name="return-list"),
    path("returns/<uuid:return_id>/", views.sale_return_detail, name="return-detail"),
    path(
        "returns/<uuid:return_id>/edit/",
        views.sale_return_edit,
        name="return-edit",
    ),
    path(
        "returns/<uuid:return_id>/post/",
        views.sale_return_post,
        name="return-post",
    ),
    path(
        "returns/<uuid:return_id>/cancel/",
        views.sale_return_cancel,
        name="return-cancel",
    ),
    path(
        "returns/<uuid:return_id>/reverse/",
        views.sale_return_reverse,
        name="return-reverse",
    ),
    path(
        "return-receipts/<uuid:receipt_id>/",
        views.return_receipt_detail,
        name="return-receipt-detail",
    ),
    path("receipts/lookup/", views.receipt_lookup, name="receipt-lookup"),
    path("receipts/<uuid:receipt_id>/", views.receipt_detail, name="receipt-detail"),
]
