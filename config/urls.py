from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.accounts.views import SecureLogoutView, staff_entry_poster, staff_entry_qr
from apps.businesses.views import dashboard

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", SecureLogoutView.as_view(), name="logout"),
    path("staff-entry/", staff_entry_poster, name="staff-entry-poster"),
    path("staff-entry/qr.svg", staff_entry_qr, name="staff-entry-qr"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", dashboard, name="dashboard"),
    path("attendance/", include("apps.attendance.urls")),
    path("catalog/", include("apps.catalog.urls")),
    path("purchasing/", include("apps.purchasing.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("sales/", include("apps.sales.urls")),
    path("cash/", include("apps.cash.urls")),
    path("expenses/", include("apps.expenses.urls")),
    path("performance/", include("apps.performance.urls")),
    path("documents/", include("apps.documents.urls")),
    path("", include("apps.offline.urls")),
    path("", include("apps.public_profiles.urls")),
]
