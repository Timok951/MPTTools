from django.contrib import admin


class SoftDeleteAdmin(admin.ModelAdmin):
    list_filter = ("deleted_at",)
    actions = ["soft_delete_selected", "restore_selected"]

    def get_queryset(self, request):
        if hasattr(self.model, "all_objects"):
            return self.model.all_objects.all()
        return super().get_queryset(request)

    @admin.action(description="Soft delete selected")
    def soft_delete_selected(self, request, queryset):
        for obj in queryset:
            obj.delete()

    @admin.action(description="Restore selected")
    def restore_selected(self, request, queryset):
        for obj in queryset:
            if hasattr(obj, "restore"):
                obj.restore()
