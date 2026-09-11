from django.urls import path

from apps.attendance import views

app_name = "attendance"

urlpatterns = [
    path("", views.attendance_list, name="attendance-list"),
    path("check-in/", views.attendance_check_in, name="attendance-check-in"),
    path("check-out/", views.attendance_check_out, name="attendance-check-out"),
    path("new/", views.attendance_create, name="attendance-create"),
    path("<uuid:attendance_id>/", views.attendance_detail, name="attendance-detail"),
    path(
        "<uuid:attendance_id>/correct/",
        views.attendance_correct,
        name="attendance-correct",
    ),
]
