from django.urls import path

from apps.purchasing import views

app_name = "purchasing"

urlpatterns = [
    path("", views.purchase_list, name="purchase-list"),
    path("new/", views.purchase_create, name="purchase-create"),
    path("<uuid:purchase_id>/", views.purchase_detail, name="purchase-detail"),
    path("<uuid:purchase_id>/edit/", views.purchase_edit, name="purchase-edit"),
    path("<uuid:purchase_id>/approve/", views.purchase_approve, name="purchase-approve"),
    path("<uuid:purchase_id>/cancel/", views.purchase_cancel, name="purchase-cancel"),
    path("<uuid:purchase_id>/receive/", views.purchase_receive, name="purchase-receive"),
    path("suppliers/", views.supplier_list, name="supplier-list"),
    path("suppliers/new/", views.supplier_create, name="supplier-create"),
    path("suppliers/<uuid:supplier_id>/edit/", views.supplier_edit, name="supplier-edit"),
]
