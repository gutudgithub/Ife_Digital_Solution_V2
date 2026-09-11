from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.product_list, name="product-list"),
    path("new/", views.product_create, name="product-create"),
    path("variants/", views.variant_list, name="variant-list"),
    path("variants/new/", views.variant_create, name="variant-create"),
    path("variants/<uuid:variant_id>/edit/", views.variant_edit, name="variant-edit"),
]
