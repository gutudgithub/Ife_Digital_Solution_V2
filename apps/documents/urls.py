from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("", views.document_list, name="list"),
    path("capture/", views.document_capture, name="capture"),
    path("<uuid:document_id>/", views.document_detail, name="detail"),
    path(
        "<uuid:document_id>/cancel/",
        views.document_cancel,
        name="cancel",
    ),
    path(
        "<uuid:document_id>/retry-scan/",
        views.document_retry_scan,
        name="retry-scan",
    ),
    path(
        "<uuid:document_id>/files/<uuid:file_id>/preview/",
        views.document_file_preview,
        name="file-preview",
    ),
    path(
        "<uuid:document_id>/files/<uuid:file_id>/download/",
        views.document_file_download,
        name="file-download",
    ),
    path(
        "transcriptions/<uuid:transcription_id>/edit/",
        views.transcription_edit,
        name="transcription-edit",
    ),
    path(
        "transcriptions/<uuid:transcription_id>/submit/",
        views.transcription_submit,
        name="transcription-submit",
    ),
    path(
        "transcriptions/<uuid:transcription_id>/confirm/",
        views.transcription_confirm,
        name="transcription-confirm",
    ),
    path(
        "transcriptions/<uuid:transcription_id>/return/",
        views.transcription_return,
        name="transcription-return",
    ),
    path(
        "transcriptions/<uuid:transcription_id>/cancel/",
        views.transcription_cancel,
        name="transcription-cancel",
    ),
    path(
        "transcriptions/<uuid:transcription_id>/replace/",
        views.transcription_replace,
        name="transcription-replace",
    ),
    path(
        "transcriptions/<uuid:transcription_id>/post-opening-stock/",
        views.transcription_post_opening_stock,
        name="transcription-post-opening-stock",
    ),
]
