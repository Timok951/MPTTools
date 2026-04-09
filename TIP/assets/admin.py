from django.contrib import admin
from django.utils import timezone

from core.admin_utils import SoftDeleteAdmin
from .models import Equipment, EquipmentCheckout, InventoryAdjustment


@admin.register(Equipment)
class EquipmentAdmin(SoftDeleteAdmin):
    actions = SoftDeleteAdmin.actions + ["mark_inventory_today"]
    list_display = (
        "name",
        "inventory_number",
        "category",
        "workplace",
        "cabinet",
        "quantity_total",
        "quantity_available",
        "is_consumable",
        "status",
        "is_low_stock",
        "deleted_at",
    )
    list_filter = ("status", "is_consumable", "category", "workplace", "cabinet", "deleted_at")
    search_fields = ("name", "inventory_number", "serial_number", "model")

    @admin.action(description="Mark inventory checked today")
    def mark_inventory_today(self, request, queryset):
        queryset.update(last_inventory_at=timezone.now().date())


@admin.register(InventoryAdjustment)
class InventoryAdjustmentAdmin(SoftDeleteAdmin):
    list_display = ("id", "equipment", "delta", "reason", "created_by", "created_at", "deleted_at")
    list_filter = ("created_at", "deleted_at")
    search_fields = ("equipment__name", "reason", "created_by__username")


@admin.register(EquipmentCheckout)
class EquipmentCheckoutAdmin(SoftDeleteAdmin):
    list_display = (
        "id",
        "equipment",
        "related_request",
        "taken_by",
        "quantity",
        "taken_at",
        "due_at",
        "returned_at",
        "deleted_at",
    )
    list_filter = ("taken_at", "returned_at", "deleted_at")
    search_fields = ("equipment__name", "taken_by__username", "note")
