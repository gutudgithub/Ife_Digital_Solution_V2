from django.urls import path

from apps.inventory import views

app_name = "inventory"

urlpatterns = [
    path("", views.inventory_list, name="inventory-list"),
    path("opening/", views.opening_balance_create, name="opening-create"),
    path("adjust/", views.inventory_adjust, name="inventory-adjust"),
]
