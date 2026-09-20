from django.urls import path

from apps.performance import views

app_name = "performance"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("print/", views.performance_print, name="print"),
    path("time-series.csv", views.time_series_csv, name="time-series-csv"),
    path("products.csv", views.product_performance_csv, name="products-csv"),
]
