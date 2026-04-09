from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.schemas import get_schema_view

from inventory.api.viewsets import (
    CabinetViewSet,
    EquipmentCategoryViewSet,
    EquipmentCheckoutViewSet,
    EquipmentRequestViewSet,
    EquipmentViewSet,
    MaterialUsageViewSet,
    SupplierViewSet,
    WorkTimerViewSet,
    WorkplaceViewSet,
)

router = DefaultRouter()
router.register("equipment", EquipmentViewSet, basename="api-equipment")
router.register("workplaces", WorkplaceViewSet, basename="api-workplace")
router.register("cabinets", CabinetViewSet, basename="api-cabinet")
router.register("categories", EquipmentCategoryViewSet, basename="api-category")
router.register("suppliers", SupplierViewSet, basename="api-supplier")
router.register("requests", EquipmentRequestViewSet, basename="api-request")
router.register("usage", MaterialUsageViewSet, basename="api-usage")
router.register("checkouts", EquipmentCheckoutViewSet, basename="api-checkout")
router.register("timers", WorkTimerViewSet, basename="api-timer")

schema_view = get_schema_view(
    title="TIP Inventory API",
    description="Read-only API for inventory, reference, and operations data.",
    version="1.0.0",
)

urlpatterns = [
    path("", include(router.urls)),
    path("schema/", schema_view, name="api-schema"),
]
