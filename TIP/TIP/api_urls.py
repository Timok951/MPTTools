from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter
from rest_framework.schemas import get_schema_view

from inventory.api.auth_views import TokenRevokeView
from inventory.api.viewsets import (
    CabinetViewSet,
    EquipmentCategoryViewSet,
    EquipmentCheckoutViewSet,
    InventoryAdjustmentViewSet,
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
router.register("adjustments", InventoryAdjustmentViewSet, basename="api-adjustment")
router.register("checkouts", EquipmentCheckoutViewSet, basename="api-checkout")
router.register("timers", WorkTimerViewSet, basename="api-timer")

schema_view = get_schema_view(
    title="TIP Inventory API",
    description="Session and token authenticated API for inventory, reference, and operations data with role-based CRUD access.",
    version="1.0.0",
)

urlpatterns = [
    path("", include(router.urls)),
    path("auth/token/", obtain_auth_token, name="api-token-auth"),
    path("auth/token/revoke/", TokenRevokeView.as_view(), name="api-token-revoke"),
    path("schema/", schema_view, name="api-schema"),
]
