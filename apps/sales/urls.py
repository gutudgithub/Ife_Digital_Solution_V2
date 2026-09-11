from django.urls import path

from apps.sales import views

app_name = "sales"

urlpatterns = [
    path("", views.sale_list, name="sale-list"),
    path("new/", views.sale_create, name="sale-create"),
    path("<uuid:sale_id>/", views.sale_detail, name="sale-detail"),
    path("<uuid:sale_id>/edit/", views.sale_edit, name="sale-edit"),
    path("<uuid:sale_id>/post/", views.sale_post, name="sale-post"),
    path("<uuid:sale_id>/cancel/", views.sale_cancel, name="sale-cancel"),
    path("receipts/lookup/", views.receipt_lookup, name="receipt-lookup"),
    path("receipts/<uuid:receipt_id>/", views.receipt_detail, name="receipt-detail"),
]
