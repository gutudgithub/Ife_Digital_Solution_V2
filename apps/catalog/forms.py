from typing import cast

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Business
from apps.catalog.models import Product, ProductVariant


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ("name", "category", "description")


class ProductVariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = (
            "product",
            "sku",
            "size",
            "color",
            "selling_price",
            "cost_price",
            "stock_unit",
            "low_stock_threshold",
            "is_active",
        )
        help_texts = {
            "cost_price": _(
                "Optional reference cost only. Posted branch movements determine inventory cost."
            ),
            "low_stock_threshold": _("Leave blank when no low-stock alert is needed."),
        }
        labels = {"cost_price": _("Reference cost")}

    def scope_to_business(self, business: Business, *, stock_unit_locked: bool) -> None:
        product_field = cast(forms.ModelChoiceField, self.fields["product"])
        product_field.queryset = Product.objects.filter(business=business, is_active=True)
        if stock_unit_locked:
            self.fields["stock_unit"].disabled = True
            self.fields["stock_unit"].help_text = _(
                "Stock unit cannot change while an approved purchase is open or after "
                "the first posted movement."
            )
