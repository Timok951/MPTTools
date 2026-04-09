from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "object_repr", "model_label")
    list_filter = ("action", "content_type", "created_at")
    search_fields = ("object_repr", "actor__username")
    readonly_fields = ("created_at", "action", "actor", "content_type", "object_id", "object_repr", "meta")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
