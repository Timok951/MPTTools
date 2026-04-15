from django.contrib import admin

from .admin_utils import SoftDeleteAdmin
from .models import Cabinet, DirectMessage, EquipmentCategory, PasswordResetCode, Supplier, UserPreference, Workplace, WorkplaceMember


@admin.register(Supplier)
class SupplierAdmin(SoftDeleteAdmin):
    list_display = ("name", "contact_name", "phone", "email", "deleted_at")
    search_fields = ("name", "contact_name", "phone", "email")


@admin.register(EquipmentCategory)
class EquipmentCategoryAdmin(SoftDeleteAdmin):
    list_display = ("name", "deleted_at")
    search_fields = ("name",)


@admin.register(Workplace)
class WorkplaceAdmin(SoftDeleteAdmin):
    list_display = ("name", "location", "deleted_at")
    search_fields = ("name", "location")


@admin.register(Cabinet)
class CabinetAdmin(SoftDeleteAdmin):
    list_display = ("code", "name", "workplace", "floor", "deleted_at")
    search_fields = ("code", "name", "workplace__name")


@admin.register(WorkplaceMember)
class WorkplaceMemberAdmin(SoftDeleteAdmin):
    list_display = ("workplace", "user", "role", "assigned_at", "deleted_at")
    list_filter = ("role", "workplace", "deleted_at")
    search_fields = ("workplace__name", "user__username", "user__email")


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "theme_variant", "page_size", "date_display_format", "default_timer_status", "updated_at")
    list_filter = ("theme_variant", "page_size", "date_display_format", "default_timer_status")
    search_fields = ("user__username", "user__email")


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    list_display = ("sender", "recipient", "created_at", "read_at")
    list_filter = ("created_at", "read_at")
    search_fields = ("sender__username", "recipient__username", "body")


@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "email", "created_at", "expires_at", "used_at")
    list_filter = ("created_at", "expires_at", "used_at")
    search_fields = ("user__username", "user__email", "email")
