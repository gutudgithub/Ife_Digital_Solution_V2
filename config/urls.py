from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.businesses.views import dashboard

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", dashboard, name="dashboard"),
    path("attendance/", include("apps.attendance.urls")),
    path("catalog/", include("apps.catalog.urls")),
    path("purchasing/", include("apps.purchasing.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("sales/", include("apps.sales.urls")),
    path("cash/", include("apps.cash.urls")),
]
