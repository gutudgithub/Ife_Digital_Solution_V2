from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.product_list, name="product-list"),
    path("new/", views.product_create, name="product-create"),
]
