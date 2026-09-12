from django.urls import path

from apps.cash import views

app_name = "cash"

urlpatterns = [
    path("", views.session_list, name="session-list"),
    path("open/", views.session_open, name="session-open"),
    path("<uuid:session_id>/", views.session_detail, name="session-detail"),
    path(
        "<uuid:session_id>/movement/",
        views.manual_movement,
        name="manual-movement",
    ),
    path("<uuid:session_id>/close/", views.session_close, name="session-close"),
    path("<uuid:session_id>/reopen/", views.session_reopen, name="session-reopen"),
    path(
        "closures/<uuid:closure_id>/",
        views.closure_report,
        name="closure-report",
    ),
]
