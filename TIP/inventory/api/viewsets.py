from rest_framework import viewsets

from assets.models import Equipment, EquipmentCheckout
from core.models import Cabinet, EquipmentCategory, Supplier, Workplace
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer

from .permissions import CanAccessInventoryApi
from .serializers import (
    CabinetSerializer,
    EquipmentCategorySerializer,
    EquipmentCheckoutSerializer,
    EquipmentRequestSerializer,
    EquipmentSerializer,
    MaterialUsageSerializer,
    SupplierSerializer,
    WorkTimerSerializer,
    WorkplaceSerializer,
)


class InventoryModelViewSet(viewsets.ModelViewSet):
    permission_classes = [CanAccessInventoryApi]


class EquipmentViewSet(InventoryModelViewSet):
    serializer_class = EquipmentSerializer

    def get_queryset(self):
        return Equipment.objects.select_related("category", "supplier", "workplace", "cabinet").order_by("name", "inventory_number")


class WorkplaceViewSet(InventoryModelViewSet):
    queryset = Workplace.objects.order_by("name")
    serializer_class = WorkplaceSerializer


class CabinetViewSet(InventoryModelViewSet):
    queryset = Cabinet.objects.select_related("workplace").order_by("code")
    serializer_class = CabinetSerializer


class EquipmentCategoryViewSet(InventoryModelViewSet):
    queryset = EquipmentCategory.objects.order_by("name")
    serializer_class = EquipmentCategorySerializer


class SupplierViewSet(InventoryModelViewSet):
    queryset = Supplier.objects.order_by("name")
    serializer_class = SupplierSerializer


class EquipmentRequestViewSet(InventoryModelViewSet):
    queryset = EquipmentRequest.objects.select_related("requester", "workplace", "equipment", "processed_by").order_by("-requested_at")
    serializer_class = EquipmentRequestSerializer

    def perform_create(self, serializer):
        instance = serializer.save(requester=self.request.user)
        instance._actor = self.request.user
        instance.save()

    def perform_update(self, serializer):
        instance = serializer.save()
        instance._actor = self.request.user
        instance.save()


class MaterialUsageViewSet(InventoryModelViewSet):
    queryset = MaterialUsage.objects.select_related("equipment", "workplace", "used_by", "related_request").order_by("-used_at")
    serializer_class = MaterialUsageSerializer

    def perform_create(self, serializer):
        instance = serializer.save(used_by=self.request.user)
        instance._actor = self.request.user
        instance.save()

    def perform_update(self, serializer):
        instance = serializer.save()
        instance._actor = self.request.user
        instance.save()


class EquipmentCheckoutViewSet(InventoryModelViewSet):
    queryset = EquipmentCheckout.objects.select_related("equipment", "taken_by", "workplace", "cabinet", "related_request").order_by("-taken_at")
    serializer_class = EquipmentCheckoutSerializer

    def perform_create(self, serializer):
        instance = serializer.save(taken_by=self.request.user)
        instance._actor = self.request.user
        instance.save()

    def perform_update(self, serializer):
        instance = serializer.save()
        instance._actor = self.request.user
        instance.save()


class WorkTimerViewSet(InventoryModelViewSet):
    queryset = WorkTimer.objects.select_related("user", "workplace", "equipment").order_by("-started_at")
    serializer_class = WorkTimerSerializer

    def perform_create(self, serializer):
        instance = serializer.save(user=self.request.user)
        instance._actor = self.request.user
        instance.save()

    def perform_update(self, serializer):
        instance = serializer.save()
        instance._actor = self.request.user
        instance.save()
