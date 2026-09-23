from django.urls import path

from apps.offline import views

app_name = "offline"

urlpatterns = [
    path("app.webmanifest", views.manifest, name="manifest"),
    path("offline/service-worker.js", views.service_worker, name="service-worker"),
    path("offline/", views.unavailable, name="unavailable"),
    path("offline/sales/", views.offline_sales, name="sales"),
    path("offline/sales/catalog/", views.catalog_snapshot, name="catalog"),
    path("offline/sales/sync/", views.sync_sale, name="sync-sale"),
]
