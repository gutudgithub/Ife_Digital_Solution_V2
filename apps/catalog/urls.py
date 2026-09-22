from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.product_list, name="product-list"),
    path("new/", views.product_create, name="product-create"),
    path(
        "products/<uuid:product_id>/image/",
        views.product_image_manage,
        name="product-image-manage",
    ),
    path(
        "products/<uuid:product_id>/image/remove/",
        views.product_image_remove,
        name="product-image-remove",
    ),
    path("media/<uuid:image_id>/", views.product_image, name="product-image"),
    path("variants/", views.variant_list, name="variant-list"),
    path("variants/new/", views.variant_create, name="variant-create"),
    path("variants/<uuid:variant_id>/edit/", views.variant_edit, name="variant-edit"),
]
