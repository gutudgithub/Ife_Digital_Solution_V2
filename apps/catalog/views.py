from typing import cast
from uuid import UUID

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Count, Prefetch
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from apps.businesses.models import BusinessMembership
from apps.businesses.types import TenantRequest
from apps.catalog.forms import ProductForm, ProductImageForm, ProductVariantForm
from apps.catalog.models import Category, Product, ProductImage, ProductVariant
from apps.catalog.services import remove_product_image, set_product_image
from apps.forms import add_accessible_error_attributes
from apps.inventory.models import InventoryMovement
from apps.purchasing.models import PurchaseLine, PurchaseStatus
from config.rate_limit import rate_limit


@login_required
def product_list(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))

    current_images = ProductImage.objects.filter(removed_at__isnull=True)
    variants = ProductVariant.objects.order_by("size", "color", "sku")
    products = (
        Product.objects.filter(business=tenant_request.active_business)
        .select_related("category")
        .prefetch_related(
            Prefetch("images", queryset=current_images, to_attr="current_images"),
            Prefetch("variants", queryset=variants),
        )
        .annotate(variant_count=Count("variants", distinct=True))
    )
    return render(
        request,
        "catalog/product_list.html",
        {
            "products": products,
            "can_manage_catalog": tenant_request.active_membership.can_manage_catalog,
            "can_view_inventory_cost": (tenant_request.active_membership.can_view_inventory_cost),
        },
    )


@login_required
def product_create(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    business = tenant_request.active_business
    if membership is None or business is None or not membership.can_manage_catalog:
        raise PermissionDenied(_("Catalog management permission is required."))

    form = ProductForm(request.POST or None)
    form.instance.business = business
    category_field = form.fields["category"]
    if not isinstance(category_field, forms.ModelChoiceField):
        raise TypeError("Product category must be a model choice field.")
    category_field.queryset = Category.objects.filter(business=business, is_active=True)

    if request.method == "POST" and form.is_valid():
        product = form.save(commit=False)
        product.business = business
        product.save()
        messages.success(request, _("Product created."))
        return redirect("catalog:product-list")

    add_accessible_error_attributes(form)
    return render(request, "catalog/product_form.html", {"form": form})


def _catalog_membership(request: HttpRequest) -> BusinessMembership:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    if membership is None or tenant_request.active_business is None:
        raise PermissionDenied(_("No active business membership is available."))
    return membership


@login_required
@rate_limit(
    scope="product-image-upload",
    limit=settings.UPLOAD_RATE_LIMIT,
    window_seconds=settings.UPLOAD_RATE_LIMIT_WINDOW_SECONDS,
)
def product_image_manage(request: HttpRequest, product_id: UUID) -> HttpResponse:
    membership = _catalog_membership(request)
    if not membership.can_manage_catalog:
        raise PermissionDenied(_("Catalog management permission is required."))
    product = get_object_or_404(
        Product.objects.prefetch_related(
            Prefetch(
                "images",
                queryset=ProductImage.objects.filter(removed_at__isnull=True),
                to_attr="current_images",
            )
        ),
        pk=product_id,
        business=membership.business,
    )
    form = ProductImageForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["image"]
        if not isinstance(upload, UploadedFile):
            raise TypeError("Validated product image must be an uploaded file.")
        try:
            set_product_image(
                actor=membership,
                product=product,
                upload=upload,
                alt_text=str(form.cleaned_data["alt_text"]),
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Product image saved."))
            return redirect("catalog:product-list")
    add_accessible_error_attributes(form)
    return render(
        request,
        "catalog/product_image_form.html",
        {"form": form, "product": product},
    )


@login_required
@require_POST
def product_image_remove(request: HttpRequest, product_id: UUID) -> HttpResponse:
    membership = _catalog_membership(request)
    product = get_object_or_404(Product, pk=product_id, business=membership.business)
    try:
        remove_product_image(actor=membership, product=product)
    except ProductImage.DoesNotExist:
        raise Http404 from None
    messages.success(request, _("Product image removed."))
    return redirect("catalog:product-list")


@login_required
@require_safe
def product_image(request: HttpRequest, image_id: UUID) -> FileResponse:
    membership = _catalog_membership(request)
    image = get_object_or_404(
        ProductImage.objects.select_related("product"),
        pk=image_id,
        business=membership.business,
        removed_at__isnull=True,
    )
    try:
        response = FileResponse(image.source.open("rb"), content_type=image.media_type)
    except (FileNotFoundError, OSError):
        raise Http404 from None
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = 'inline; filename="product.webp"'
    return response


@login_required
def variant_list(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    business = tenant_request.active_business
    if membership is None or business is None:
        raise PermissionDenied(_("No active business membership is available."))
    variants = ProductVariant.objects.filter(business=business).select_related("product")
    return render(
        request,
        "catalog/variant_list.html",
        {
            "variants": variants,
            "can_manage_catalog": membership.can_manage_catalog,
            "can_view_inventory_cost": membership.can_view_inventory_cost,
        },
    )


def _variant_form_response(
    request: HttpRequest,
    *,
    variant: ProductVariant,
    stock_unit_locked: bool,
) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    business = tenant_request.active_business
    if membership is None or business is None or not membership.can_manage_catalog:
        raise PermissionDenied(_("Catalog management permission is required."))
    form = ProductVariantForm(request.POST or None, instance=variant)
    form.scope_to_business(business, stock_unit_locked=stock_unit_locked)
    if request.method == "POST" and form.is_valid():
        saved_variant = form.save(commit=False)
        saved_variant.business = business
        saved_variant.full_clean()
        saved_variant.save()
        messages.success(request, _("Product variant saved."))
        return redirect("catalog:variant-list")
    add_accessible_error_attributes(form)
    return render(
        request,
        "catalog/variant_form.html",
        {"form": form, "variant": variant},
    )


@login_required
def variant_create(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None:
        raise PermissionDenied(_("No active business membership is available."))
    return _variant_form_response(
        request,
        variant=ProductVariant(business=tenant_request.active_business),
        stock_unit_locked=False,
    )


@login_required
def variant_edit(request: HttpRequest, variant_id: UUID) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    business = tenant_request.active_business
    if business is None:
        raise PermissionDenied(_("No active business membership is available."))
    variant = get_object_or_404(ProductVariant, pk=variant_id, business=business)
    stock_unit_locked = (
        InventoryMovement.objects.filter(business=business, variant=variant).exists()
        or PurchaseLine.objects.filter(
            business=business,
            variant=variant,
            purchase__status__in=(
                PurchaseStatus.APPROVED,
                PurchaseStatus.PARTIALLY_RECEIVED,
            ),
        ).exists()
    )
    return _variant_form_response(
        request,
        variant=variant,
        stock_unit_locked=stock_unit_locked,
    )
