from django.urls import path

from apps.public_profiles import views

app_name = "public_profiles"

urlpatterns = [
    path("public-profile/", views.profile_manage, name="manage"),
    path("public-profile/profile/", views.profile_update, name="profile-update"),
    path("public-profile/hours/", views.opening_hours_update, name="hours-update"),
    path("public-profile/links/", views.contact_links_update, name="links-update"),
    path(
        "public-profile/products/<uuid:product_id>/",
        views.product_publication_update,
        name="product-update",
    ),
    path("public-profile/publish/", views.profile_publish, name="publish"),
    path("public-profile/unpublish/", views.profile_unpublish, name="unpublish"),
    path("public-profile/indexing/", views.indexing_update, name="indexing"),
    path(
        "public-profile/verification/",
        views.verification_request_create,
        name="verification-request",
    ),
    path("public-profile/preview/", views.profile_preview, name="preview"),
    path("public-profile/qr.svg", views.profile_qr, name="profile-qr"),
    path("public-profile/poster/", views.profile_poster, name="poster"),
    path(
        "public-profile/products/<uuid:product_id>/qr.svg",
        views.product_qr,
        name="product-qr",
    ),
    path(
        "platform/public-profiles/",
        views.staff_verification_queue,
        name="staff-queue",
    ),
    path(
        "platform/public-profiles/requests/<uuid:request_id>/decide/",
        views.staff_verification_decide,
        name="staff-decide",
    ),
    path(
        "platform/public-profiles/<uuid:profile_id>/verification/",
        views.staff_verification_lifecycle,
        name="staff-verification-lifecycle",
    ),
    path(
        "platform/public-profiles/<uuid:profile_id>/suspend/",
        views.staff_profile_suspend,
        name="staff-suspend",
    ),
    path(
        "platform/public-profiles/<uuid:profile_id>/reinstate/",
        views.staff_profile_reinstate,
        name="staff-reinstate",
    ),
    path("p/<uuid:public_id>/", views.public_profile, name="public-profile"),
    path(
        "p/<uuid:public_id>/products/<uuid:product_public_id>/",
        views.public_product,
        name="public-product",
    ),
    path(
        "verify/receipts/sale/<uuid:token>/",
        views.verify_sale_receipt,
        name="verify-sale-receipt",
    ),
    path(
        "verify/receipts/return/<uuid:token>/",
        views.verify_return_receipt,
        name="verify-return-receipt",
    ),
    path(
        "verify/receipts/sale/<uuid:token>/qr.svg",
        views.sale_receipt_qr,
        name="sale-receipt-qr",
    ),
    path(
        "verify/receipts/return/<uuid:token>/qr.svg",
        views.return_receipt_qr,
        name="return-receipt-qr",
    ),
    path("robots.txt", views.robots_txt, name="robots"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap"),
]
