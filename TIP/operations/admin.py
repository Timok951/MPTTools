from django.contrib import admin

from core.admin_utils import SoftDeleteAdmin
from .models import EquipmentRequest, MaterialUsage


@admin.register(EquipmentRequest)
class EquipmentRequestAdmin(SoftDeleteAdmin):
    list_display = (
        "id",
        "requester",
        "request_kind",
        "equipment",
        "quantity",
        "status",
        "requested_at",
        "processed_at",
        "deleted_at",
    )
    list_filter = ("status", "request_kind", "requested_at", "deleted_at")
    search_fields = ("requester__username", "equipment__name", "comment")


@admin.register(MaterialUsage)
class MaterialUsageAdmin(SoftDeleteAdmin):
    list_display = ("id", "equipment", "quantity", "used_by", "used_at", "workplace", "deleted_at")
    list_filter = ("used_at", "workplace", "deleted_at")
    search_fields = ("equipment__name", "used_by__username", "note")
