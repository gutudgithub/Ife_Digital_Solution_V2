from datetime import date, time, timedelta
from typing import cast
from uuid import UUID
from xml.etree.ElementTree import Element, SubElement, tostring

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.forms.formsets import BaseFormSet
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from apps.businesses.models import BusinessMembership
from apps.businesses.types import TenantRequest
from apps.catalog.models import Product, ProductImage
from apps.forms import add_accessible_error_attributes
from apps.public_profiles.forms import (
    ContactLinkFormSet,
    IndexingPreferenceForm,
    OpeningHourFormSet,
    ProductPublicationForm,
    PublicProfileForm,
    SuspensionForm,
    VerificationDecisionForm,
    VerificationLifecycleForm,
    VerificationRequestForm,
)
from apps.public_profiles.models import (
    ContactLinkType,
    MetricKind,
    MetricSource,
    MetricTargetType,
    PublicationStatus,
    PublicBusinessProfile,
    PublicOpeningHour,
    PublicProductIdentity,
    PublicReturnReceiptIdentity,
    PublicSaleReceiptIdentity,
    PublicVerificationRequest,
    VerificationRequestStatus,
)
from apps.public_profiles.services import (
    ContactLinkData,
    OpeningHourData,
    ProfileDraftData,
    absolute_public_url,
    build_profile_preview,
    decide_verification_request,
    expire_verification,
    get_or_create_profile,
    get_product_model_for_public_id,
    get_profile_model_for_public_id,
    get_public_product,
    get_public_profile,
    increment_metric,
    metric_source,
    product_public_url,
    profile_public_url,
    public_origin,
    public_return_receipt,
    public_sale_receipt,
    publish_profile,
    qr_svg,
    reinstate_profile,
    renew_verification,
    return_receipt_public_url,
    revoke_verification,
    sale_receipt_public_url,
    set_product_publication,
    set_search_indexing,
    submit_verification_request,
    suspend_profile,
    unpublish_profile,
    update_contact_links,
    update_opening_hours,
    update_profile,
)
from config.rate_limit import rate_limit


def _tenant_manager(request: HttpRequest) -> BusinessMembership:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    if (
        membership is None
        or tenant_request.active_business is None
        or not membership.can_manage_public_profile
    ):
        raise PermissionDenied(_("Public profile management permission is required."))
    return membership


def _profile_form(profile: PublicBusinessProfile) -> PublicProfileForm:
    return PublicProfileForm(
        initial={
            "display_name": profile.display_name,
            "description": profile.description,
            "phone": profile.phone,
            "email": profile.email,
            "website": profile.website,
            "address": profile.address,
            "map_url": profile.map_url,
            "supported_languages": profile.supported_languages.split(","),
        }
    )


def _opening_hour_formset(profile: PublicBusinessProfile) -> BaseFormSet:
    existing = {hour.weekday: hour for hour in profile.opening_hours.all()}
    initial: list[dict[str, object]] = []
    for weekday, label in PublicOpeningHour.Weekday.choices:
        hour = existing.get(weekday)
        initial.append(
            {
                "weekday": weekday,
                "weekday_label": label,
                "is_closed": hour.is_closed if hour else True,
                "opens_at": hour.opens_at if hour else None,
                "closes_at": hour.closes_at if hour else None,
            }
        )
    return OpeningHourFormSet(initial=initial, prefix="hours")


def _contact_link_formset(profile: PublicBusinessProfile) -> BaseFormSet:
    existing = {link.link_type: link for link in profile.contact_links.all()}
    initial: list[dict[str, object]] = []
    for link_type, label in ContactLinkType.choices:
        link = existing.get(link_type)
        initial.append(
            {
                "link_type": link_type,
                "label": link.label if link else label,
                "url": link.url if link else "",
                "is_active": link.is_active if link else True,
            }
        )
    return ContactLinkFormSet(initial=initial, prefix="links")


def _readiness(profile: PublicBusinessProfile) -> tuple[str, ...]:
    missing: list[str] = []
    if not profile.display_name.strip():
        missing.append(str(_("Public display name")))
    if not profile.description.strip():
        missing.append(str(_("Description")))
    if not (
        profile.phone
        or profile.email
        or profile.website
        or profile.contact_links.filter(is_active=True).exists()
    ):
        missing.append(str(_("At least one public contact method")))
    return tuple(missing)


def _manage_context(
    *,
    membership: BusinessMembership,
    profile: PublicBusinessProfile,
    profile_form: PublicProfileForm | None = None,
    hour_formset: BaseFormSet | None = None,
    link_formset: BaseFormSet | None = None,
    verification_form: VerificationRequestForm | None = None,
) -> dict[str, object]:
    today = timezone.localdate()
    metric_rows = (
        profile.daily_metrics.filter(local_date__gte=today - timedelta(days=29))
        .values("local_date", "metric", "source")
        .annotate(total=Sum("count"))
        .order_by("-local_date", "metric", "source")
    )
    products = Product.objects.filter(business=membership.business).order_by("name")
    return {
        "profile": profile,
        "profile_form": profile_form or _profile_form(profile),
        "hour_formset": hour_formset or _opening_hour_formset(profile),
        "link_formset": link_formset or _contact_link_formset(profile),
        "verification_form": verification_form or VerificationRequestForm(),
        "products": products,
        "requests": profile.verification_requests.select_related("requester__user")[:20],
        "events": profile.events.select_related(
            "actor_membership__user",
            "actor_staff",
        )[:50],
        "metric_rows": metric_rows,
        "readiness_missing": _readiness(profile),
        "is_owner": membership.can_publish_public_profile,
        "public_url": profile_public_url(profile),
    }


def _render_manage(
    *,
    request: HttpRequest,
    membership: BusinessMembership,
    profile: PublicBusinessProfile,
    status: int = 200,
    profile_form: PublicProfileForm | None = None,
    hour_formset: BaseFormSet | None = None,
    link_formset: BaseFormSet | None = None,
    verification_form: VerificationRequestForm | None = None,
) -> HttpResponse:
    return render(
        request,
        "public_profiles/manage.html",
        _manage_context(
            membership=membership,
            profile=profile,
            profile_form=profile_form,
            hour_formset=hour_formset,
            link_formset=link_formset,
            verification_form=verification_form,
        ),
        status=status,
    )


@login_required
@require_safe
def profile_manage(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    return _render_manage(request=request, membership=membership, profile=profile)


@login_required
@require_POST
def profile_update(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    form = PublicProfileForm(request.POST)
    add_accessible_error_attributes(form)
    if not form.is_valid():
        return _render_manage(
            request=request,
            membership=membership,
            profile=profile,
            profile_form=form,
            status=400,
        )
    update_profile(
        actor=membership,
        profile=profile,
        data=ProfileDraftData(
            display_name=cast(str, form.cleaned_data["display_name"]),
            description=cast(str, form.cleaned_data["description"]),
            phone=cast(str, form.cleaned_data["phone"]),
            email=cast(str, form.cleaned_data["email"]),
            website=cast(str, form.cleaned_data["website"]),
            address=cast(str, form.cleaned_data["address"]),
            map_url=cast(str, form.cleaned_data["map_url"]),
            supported_languages=tuple(cast(list[str], form.cleaned_data["supported_languages"])),
        ),
    )
    messages.success(request, _("Public profile draft updated."))
    return redirect("public_profiles:manage")


@login_required
@require_POST
def opening_hours_update(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    formset = OpeningHourFormSet(request.POST, prefix="hours")
    if not formset.is_valid():
        return _render_manage(
            request=request,
            membership=membership,
            profile=profile,
            hour_formset=formset,
            status=400,
        )
    hours = tuple(
        OpeningHourData(
            weekday=cast(int, form.cleaned_data["weekday"]),
            is_closed=cast(bool, form.cleaned_data["is_closed"]),
            opens_at=cast(time | None, form.cleaned_data["opens_at"]),
            closes_at=cast(time | None, form.cleaned_data["closes_at"]),
        )
        for form in formset.forms
    )
    update_opening_hours(actor=membership, profile=profile, hours=hours)
    messages.success(request, _("Opening hours updated."))
    return redirect("public_profiles:manage")


@login_required
@require_POST
def contact_links_update(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    formset = ContactLinkFormSet(request.POST, prefix="links")
    if not formset.is_valid():
        return _render_manage(
            request=request,
            membership=membership,
            profile=profile,
            link_formset=formset,
            status=400,
        )
    links = tuple(
        ContactLinkData(
            link_type=cast(str, form.cleaned_data["link_type"]),
            label=cast(str, form.cleaned_data["label"]),
            url=cast(str, form.cleaned_data["url"]),
            display_order=index,
            is_active=cast(bool, form.cleaned_data["is_active"]),
        )
        for index, form in enumerate(formset.forms)
    )
    update_contact_links(actor=membership, profile=profile, links=links)
    messages.success(request, _("Public contact links updated."))
    return redirect("public_profiles:manage")


@login_required
@require_POST
def product_publication_update(request: HttpRequest, product_id: UUID) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    product = get_object_or_404(Product, pk=product_id, business=membership.business)
    form = ProductPublicationForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Review the product publication choices."))
        return redirect("public_profiles:manage")
    set_product_publication(
        actor=membership,
        profile=profile,
        product=product,
        visible=cast(bool, form.cleaned_data["visible"]),
        show_public_prices=cast(bool, form.cleaned_data["show_public_prices"]),
    )
    messages.success(request, _("Public product settings updated."))
    return redirect("public_profiles:manage")


@login_required
@require_POST
def profile_publish(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    try:
        publish_profile(actor=membership, profile=profile)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, _("Public profile published."))
    return redirect("public_profiles:manage")


@login_required
@require_POST
def profile_unpublish(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    unpublish_profile(actor=membership, profile=profile)
    messages.success(request, _("Public profile unpublished."))
    return redirect("public_profiles:manage")


@login_required
@require_POST
def indexing_update(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    form = IndexingPreferenceForm(request.POST)
    if form.is_valid():
        set_search_indexing(
            actor=membership,
            profile=profile,
            enabled=cast(bool, form.cleaned_data["enabled"]),
        )
        messages.success(request, _("Search indexing preference updated."))
    return redirect("public_profiles:manage")


@login_required
@require_POST
def verification_request_create(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    form = VerificationRequestForm(request.POST)
    add_accessible_error_attributes(form)
    if not form.is_valid():
        return _render_manage(
            request=request,
            membership=membership,
            profile=profile,
            verification_form=form,
            status=400,
        )
    parent_id = form.cleaned_data.get("parent_request_id")
    parent = None
    if parent_id is not None:
        parent = get_object_or_404(
            PublicVerificationRequest,
            pk=parent_id,
            business=membership.business,
            profile=profile,
        )
    try:
        submit_verification_request(
            actor=membership,
            profile=profile,
            indicator_type=cast(str, form.cleaned_data["indicator_type"]),
            reason=cast(str, form.cleaned_data["reason"]),
            parent_request=parent,
        )
    except ValidationError as exc:
        form.add_error(None, exc)
        return _render_manage(
            request=request,
            membership=membership,
            profile=profile,
            verification_form=form,
            status=400,
        )
    messages.success(request, _("Verification request submitted."))
    return redirect("public_profiles:manage")


@login_required
@require_safe
def profile_preview(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    page = build_profile_preview(actor=membership, profile=profile)
    response = render(
        request,
        "public_profiles/public_profile.html",
        {
            "page": page,
            "preview": True,
            "canonical_url": profile_public_url(profile),
            "support_url": settings.PUBLIC_SUPPORT_URL,
        },
    )
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_safe
def profile_qr(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    response = HttpResponse(
        qr_svg(profile_public_url(profile, source=MetricSource.QR)),
        content_type="image/svg+xml",
    )
    response["Content-Disposition"] = 'attachment; filename="business-profile-qr.svg"'
    response["Cache-Control"] = "public, max-age=86400"
    return response


@login_required
@require_safe
def product_qr(request: HttpRequest, product_id: UUID) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    identity = get_object_or_404(
        PublicProductIdentity.objects.select_related("product"),
        product_id=product_id,
        business=membership.business,
    )
    response = HttpResponse(
        qr_svg(product_public_url(profile=profile, identity=identity, source=MetricSource.QR)),
        content_type="image/svg+xml",
    )
    response["Content-Disposition"] = 'attachment; filename="product-qr.svg"'
    response["Cache-Control"] = "public, max-age=86400"
    return response


@login_required
@require_safe
def profile_poster(request: HttpRequest) -> HttpResponse:
    membership = _tenant_manager(request)
    profile = get_or_create_profile(membership)
    return render(
        request,
        "public_profiles/poster.html",
        {
            "profile": profile,
            "public_url": profile_public_url(profile, source=MetricSource.QR),
        },
    )


def _not_found() -> Http404:
    return Http404(_("Page not found."))


def _public_response(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    return response


@require_safe
@rate_limit(
    scope="public-profile",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def public_profile(request: HttpRequest, public_id: UUID) -> HttpResponse:
    try:
        profile = get_profile_model_for_public_id(public_id)
        page = get_public_profile(public_id)
    except PublicBusinessProfile.DoesNotExist as exc:
        raise _not_found() from exc
    increment_metric(
        profile=profile,
        source=metric_source(request.GET.get("source")),
        metric=MetricKind.PROFILE_VIEW,
        target_type=MetricTargetType.PROFILE,
        target_public_id=profile.public_id,
    )
    canonical_url = absolute_public_url(
        reverse("public_profiles:public-profile", args=(profile.public_id,))
    )
    response = render(
        request,
        "public_profiles/public_profile.html",
        {
            "page": page,
            "preview": False,
            "canonical_url": canonical_url,
            "support_url": settings.PUBLIC_SUPPORT_URL,
        },
    )
    response["X-Robots-Tag"] = "index, follow" if page.allow_search_indexing else "noindex"
    return _public_response(response)


@require_safe
@rate_limit(
    scope="public-product",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def public_product(
    request: HttpRequest,
    public_id: UUID,
    product_public_id: UUID,
) -> HttpResponse:
    try:
        profile = get_profile_model_for_public_id(public_id)
        product_model = get_product_model_for_public_id(
            profile=profile,
            product_public_id=product_public_id,
        )
        page, product = get_public_product(
            profile_public_id=public_id,
            product_public_id=product_public_id,
        )
    except (PublicBusinessProfile.DoesNotExist, Product.DoesNotExist) as exc:
        raise _not_found() from exc
    increment_metric(
        profile=profile,
        product=product_model,
        source=metric_source(request.GET.get("source")),
        metric=MetricKind.PRODUCT_VIEW,
        target_type=MetricTargetType.PRODUCT,
        target_public_id=product_public_id,
    )
    canonical_url = absolute_public_url(
        reverse(
            "public_profiles:public-product",
            args=(profile.public_id, product_public_id),
        )
    )
    response = render(
        request,
        "public_profiles/public_product.html",
        {
            "page": page,
            "product": product,
            "canonical_url": canonical_url,
            "support_url": settings.PUBLIC_SUPPORT_URL,
        },
    )
    response["X-Robots-Tag"] = "index, follow" if page.allow_search_indexing else "noindex"
    return _public_response(response)


@require_safe
@rate_limit(
    scope="public-product-image",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def public_product_image(
    request: HttpRequest,
    public_id: UUID,
    product_public_id: UUID,
) -> FileResponse:
    del request
    try:
        profile = get_profile_model_for_public_id(public_id)
        product = get_product_model_for_public_id(
            profile=profile,
            product_public_id=product_public_id,
        )
        image = ProductImage.objects.get(
            business=profile.business,
            product=product,
            removed_at__isnull=True,
        )
        response = FileResponse(image.source.open("rb"), content_type=image.media_type)
    except (
        FileNotFoundError,
        OSError,
        Product.DoesNotExist,
        ProductImage.DoesNotExist,
        PublicBusinessProfile.DoesNotExist,
    ) as exc:
        raise _not_found() from exc
    response["Cache-Control"] = "no-store"
    response["Content-Disposition"] = 'inline; filename="product.webp"'
    return response


@require_safe
@rate_limit(
    scope="public-sale-receipt",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def verify_sale_receipt(request: HttpRequest, token: UUID) -> HttpResponse:
    try:
        receipt = public_sale_receipt(token)
    except PublicSaleReceiptIdentity.DoesNotExist as exc:
        raise _not_found() from exc
    return _public_response(
        render(
            request,
            "public_profiles/receipt_verification.html",
            {"receipt": receipt},
        )
    )


@require_safe
@rate_limit(
    scope="public-return-receipt",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def verify_return_receipt(request: HttpRequest, token: UUID) -> HttpResponse:
    try:
        receipt = public_return_receipt(token)
    except PublicReturnReceiptIdentity.DoesNotExist as exc:
        raise _not_found() from exc
    return _public_response(
        render(
            request,
            "public_profiles/receipt_verification.html",
            {"receipt": receipt},
        )
    )


@require_safe
@rate_limit(
    scope="public-sale-receipt-qr",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def sale_receipt_qr(request: HttpRequest, token: UUID) -> HttpResponse:
    identity = get_object_or_404(PublicSaleReceiptIdentity, public_token=token)
    response = HttpResponse(
        qr_svg(sale_receipt_public_url(identity, source=MetricSource.QR)),
        content_type="image/svg+xml",
    )
    response["Cache-Control"] = "public, max-age=86400"
    return response


@require_safe
@rate_limit(
    scope="public-return-receipt-qr",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def return_receipt_qr(request: HttpRequest, token: UUID) -> HttpResponse:
    identity = get_object_or_404(PublicReturnReceiptIdentity, public_token=token)
    response = HttpResponse(
        qr_svg(return_receipt_public_url(identity, source=MetricSource.QR)),
        content_type="image/svg+xml",
    )
    response["Cache-Control"] = "public, max-age=86400"
    return response


@login_required
@require_safe
def staff_verification_queue(request: HttpRequest) -> HttpResponse:
    can_review = request.user.has_perm("public_profiles.review_public_verification")
    can_suspend = request.user.has_perm("public_profiles.suspend_public_profile")
    if not request.user.is_staff or not (can_review or can_suspend):
        raise PermissionDenied(_("Dedicated platform permission is required."))
    pending = (
        PublicVerificationRequest.objects.filter(
            status=VerificationRequestStatus.PENDING
        ).select_related("profile", "business", "requester__user")
        if can_review
        else PublicVerificationRequest.objects.none()
    )
    profiles = PublicBusinessProfile.objects.select_related("business").order_by("display_name")
    return render(
        request,
        "public_profiles/staff_queue.html",
        {
            "pending_requests": pending,
            "profiles": profiles,
            "decision_form": VerificationDecisionForm(),
            "lifecycle_form": VerificationLifecycleForm(),
            "suspension_form": SuspensionForm(),
            "can_review": can_review,
            "can_suspend": can_suspend,
        },
    )


@login_required
@permission_required(
    "public_profiles.review_public_verification",
    raise_exception=True,
)
@require_POST
def staff_verification_decide(request: HttpRequest, request_id: UUID) -> HttpResponse:
    if not request.user.is_staff:
        raise PermissionDenied(_("Platform staff status is required."))
    verification_request = get_object_or_404(PublicVerificationRequest, pk=request_id)
    form = VerificationDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Review the verification decision fields."))
        return redirect("public_profiles:staff-queue")
    decide_verification_request(
        user=request.user,
        request=verification_request,
        approved=form.cleaned_data["decision"] == "approve",
        evidence_reference=cast(str, form.cleaned_data["evidence_reference"]),
        private_reason=cast(str, form.cleaned_data["private_reason"]),
        reviewed_on=cast(date | None, form.cleaned_data["reviewed_on"]),
        expires_on=cast(date | None, form.cleaned_data["expires_on"]),
    )
    messages.success(request, _("Verification decision recorded."))
    return redirect("public_profiles:staff-queue")


@login_required
@permission_required(
    "public_profiles.review_public_verification",
    raise_exception=True,
)
@require_POST
def staff_verification_lifecycle(request: HttpRequest, profile_id: UUID) -> HttpResponse:
    if not request.user.is_staff:
        raise PermissionDenied(_("Platform staff status is required."))
    profile = get_object_or_404(PublicBusinessProfile, pk=profile_id)
    form = VerificationLifecycleForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Review the verification lifecycle fields."))
        return redirect("public_profiles:staff-queue")
    action = cast(str, form.cleaned_data["action"])
    indicator_type = cast(str, form.cleaned_data["indicator_type"])
    private_reason = cast(str, form.cleaned_data["private_reason"])
    if action == "renew":
        renew_verification(
            user=request.user,
            profile=profile,
            indicator_type=indicator_type,
            evidence_reference=cast(str, form.cleaned_data["evidence_reference"]),
            private_reason=private_reason,
            reviewed_on=cast(date | None, form.cleaned_data["reviewed_on"]),
            expires_on=cast(date | None, form.cleaned_data["expires_on"]),
        )
    elif action == "revoke":
        revoke_verification(
            user=request.user,
            profile=profile,
            indicator_type=indicator_type,
            private_reason=private_reason,
        )
    else:
        expire_verification(
            user=request.user,
            profile=profile,
            indicator_type=indicator_type,
            private_reason=private_reason,
        )
    messages.success(request, _("Verification lifecycle event recorded."))
    return redirect("public_profiles:staff-queue")


@login_required
@permission_required("public_profiles.suspend_public_profile", raise_exception=True)
@require_POST
def staff_profile_suspend(request: HttpRequest, profile_id: UUID) -> HttpResponse:
    if not request.user.is_staff:
        raise PermissionDenied(_("Platform staff status is required."))
    profile = get_object_or_404(PublicBusinessProfile, pk=profile_id)
    form = SuspensionForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("A private suspension reason is required."))
        return redirect("public_profiles:staff-queue")
    suspend_profile(
        user=request.user,
        profile=profile,
        reason=cast(str, form.cleaned_data["reason"]),
    )
    messages.success(request, _("Public profile suspended."))
    return redirect("public_profiles:staff-queue")


@login_required
@permission_required("public_profiles.suspend_public_profile", raise_exception=True)
@require_POST
def staff_profile_reinstate(request: HttpRequest, profile_id: UUID) -> HttpResponse:
    if not request.user.is_staff:
        raise PermissionDenied(_("Platform staff status is required."))
    profile = get_object_or_404(PublicBusinessProfile, pk=profile_id)
    reinstate_profile(user=request.user, profile=profile)
    messages.success(request, _("Public profile reinstated."))
    return redirect("public_profiles:staff-queue")


@require_safe
@rate_limit(
    scope="robots",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def robots_txt(request: HttpRequest) -> HttpResponse:
    sitemap_url = absolute_public_url(reverse("public_profiles:sitemap"))
    return HttpResponse(
        f"User-agent: *\nAllow: /p/\nDisallow: /public-profile/\n"
        f"Disallow: /platform/public-profiles/\nSitemap: {sitemap_url}\n",
        content_type="text/plain; charset=utf-8",
    )


@require_safe
@rate_limit(
    scope="sitemap",
    limit=lambda: settings.PUBLIC_READ_RATE_LIMIT,
    window_seconds=lambda: settings.PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS,
    methods=("GET", "HEAD"),
)
def sitemap_xml(request: HttpRequest) -> HttpResponse:
    urlset = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    profiles = PublicBusinessProfile.objects.filter(
        business__is_active=True,
        publication_status=PublicationStatus.PUBLISHED,
        is_suspended=False,
        allow_search_indexing=True,
    ).only("public_id", "updated_at")
    origin = public_origin()
    for profile in profiles:
        url = SubElement(urlset, "url")
        SubElement(
            url, "loc"
        ).text = f"{origin}{reverse('public_profiles:public-profile', args=(profile.public_id,))}"
        SubElement(url, "lastmod").text = profile.updated_at.date().isoformat()
    return HttpResponse(
        tostring(urlset, encoding="utf-8", xml_declaration=True),
        content_type="application/xml",
    )
