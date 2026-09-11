from django.contrib import admin

from apps.businesses.models import Branch, Business, BusinessMembership


class BranchInline(admin.TabularInline):
    model = Branch
    extra = 0


class MembershipInline(admin.TabularInline):
    model = BusinessMembership
    extra = 0


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("name", "business_type", "is_active")
    list_filter = ("business_type", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
    inlines = (BranchInline, MembershipInline)


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "business__name", "code")


@admin.register(BusinessMembership)
class BusinessMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "business", "assigned_branch", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = (
        "user__email",
        "user__full_name",
        "business__name",
        "assigned_branch__name",
    )
